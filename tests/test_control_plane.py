from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_actor_authorization import _Connection as AuthorizationConnection
from test_actor_authorization import _grant
from test_canonical_repository import Connection, _license_holder
from test_ingress import _envelope, _message_identity

from buyer_ops_contracts.canonical_repository import Connection as RepositoryConnection
from buyer_ops_contracts.connector_service import ConnectorDenied
from buyer_ops_contracts.control_plane import ControlPlane, IngressProviderRuntimeFactory
from buyer_ops_contracts.ingress import InboundEnvelope, RegisteredInboundEvent
from buyer_ops_contracts.ingress_service import IngressProviderRuntime
from buyer_ops_contracts.structural import validate_record


def _plane(
    connection: object,
    *,
    ingress_provider_runtime_factory: IngressProviderRuntimeFactory | None = None,
) -> ControlPlane:
    public = Ed25519PrivateKey.generate().public_key()
    plane = ControlPlane(
        "postgresql://unused",
        permit_secret=b"x" * 32,
        control_token="token",
        release_public_keys={"health-test": public},
        gate_registry_path=Path("PRODUCTION-GATE-REGISTRY.yaml"),
        ingress_provider_runtime_factory=ingress_provider_runtime_factory,
    )
    plane._connection = lambda: connection  # type: ignore[method-assign]
    return plane


def test_canonical_post_fails_closed_without_governed_command_binding() -> None:
    connection = Connection()
    connection.cursor_instance.references["person-agent-1"] = {
        "recordType": "Person",
        "tenantId": "tenant-1",
    }
    connection.cursor_instance.references["endpoint-agent-1"] = {
        "recordType": "ContactEndpoint",
        "tenantId": "tenant-1",
    }
    plane = _plane(connection)
    plane._require_actor = lambda tenant_id, actor_id: None  # type: ignore[method-assign]
    status, payload = plane.handle(
        "POST",
        "/v1/canonical",
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
            "x-buyer-ops-actor": "actor-1",
        },
        json.dumps(_license_holder()).encode(),
    )
    assert status == 422
    assert payload["code"] == "configuration_incomplete"


def test_canonical_post_requires_authenticated_actor() -> None:
    status, payload = _plane(Connection()).handle(
        "POST",
        "/v1/canonical",
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
        },
        json.dumps(_license_holder()).encode(),
    )
    assert status == 401
    assert payload["code"] == "authentication_required"


def test_tenancies_come_from_current_actor_tenant_authorization() -> None:
    connection = AuthorizationConnection()
    connection.cursor_instance.rows = [
        (_grant(effectiveAt="2020-01-01T00:00:00Z", expiresAt="2030-02-01T00:00:00Z"),)
    ]
    status, payload = _plane(connection).handle(  # type: ignore[arg-type]
        "GET",
        "/v1/actors/tenancies",
        {"x-buyer-ops-token": "token", "x-buyer-ops-actor": "actor-1"},
        b"",
    )
    assert status == 200
    assert payload == {
        "tenancies": [
            {
                "tenant_id": "tenant-1",
                "principal_id": "principal-1",
                "role": "agent",
                "authorization_id": "auth-1",
                "authorization_version": 1,
                "authorization_ref": {
                    "record_id": "auth-1",
                    "record_type": "ActorTenantAuthorization",
                    "version": 1,
                    "status": "active",
                },
                "policy_version": "policy-1",
                "policy_ref": None,
                "allowed_commands": ["request_reconciliation"],
                "record_scopes": ["journey"],
                "status": "active",
            }
        ]
    }


def test_canonical_read_unquotes_colon_ids() -> None:
    plane = _plane(Connection())
    seen: dict[str, str] = {}

    def _record(tenant_id: str, record_id: str) -> dict[str, str]:
        del tenant_id
        seen["id"] = record_id
        return {"id": record_id}

    plane._require_actor = lambda tenant_id, actor_id: None  # type: ignore[method-assign]
    plane._canonical_get = _record  # type: ignore[method-assign]
    status, payload = plane.handle(
        "GET",
        "/v1/canonical/journey%3A1111-2222",
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
            "x-buyer-ops-actor": "actor-1",
        },
        b"",
    )
    assert status == 200
    assert seen["id"] == "journey:1111-2222"
    assert payload == {"id": "journey:1111-2222"}


def test_canonical_read_decodes_id_without_rewriting_route() -> None:
    plane = _plane(Connection())
    seen: dict[str, str] = {}

    def _record(tenant_id: str, record_id: str) -> dict[str, str]:
        del tenant_id
        seen["id"] = record_id
        return {"id": record_id}

    plane._require_actor = lambda tenant_id, actor_id: None  # type: ignore[method-assign]
    plane._canonical_get = _record  # type: ignore[method-assign]
    status, payload = plane.handle(
        "GET",
        "/v1/canonical/provider%2Fjourney%3A1",
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
            "x-buyer-ops-actor": "actor-1",
        },
        b"",
    )
    assert status == 200
    assert seen["id"] == "provider/journey:1"
    assert payload == {"id": "provider/journey:1"}


def test_setup_tenant_refuses_unpublished_bootstrap_semantics() -> None:
    plane = _plane(AuthorizationConnection())
    status, payload = plane.handle(
        "POST",
        "/v1/setup/tenant",
        {"x-buyer-ops-token": "token", "x-buyer-ops-actor": "actor-1"},
        json.dumps(
            {
                "tenantId": "brokerage-live-1",
                "legalName": "Atono Brokerage",
                "licenseNumber": "9001234",
                "licenseType": "sales_agent",
                "displayName": "Samuel",
                "operatorEmail": "samuel@pyrallon.local",
                "jurisdiction": "TX",
                "emailProvider": "google_workspace",
                "calendarProvider": "google_workspace",
            }
        ).encode(),
    )
    assert status == 422
    assert payload["code"] == "configuration_incomplete"


@pytest.mark.parametrize(
    ("method", "route"),
    [
        ("GET", "/v1/workspace"),
        ("GET", "/v1/workspace/journeys/journey-1"),
        ("POST", "/v1/workspace/appointments"),
        ("POST", "/v1/workspace/assertions"),
        ("POST", "/v1/workspace/suppressions"),
    ],
)
def test_workspace_routes_refuse_unpublished_projection_and_mutation_semantics(
    method: str, route: str
) -> None:
    plane = _plane(AuthorizationConnection())
    plane._require_actor = lambda tenant_id, actor_id: None  # type: ignore[method-assign]
    status, payload = plane.handle(
        method,
        route,
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
            "x-buyer-ops-actor": "actor-1",
        },
        b"{}" if method == "POST" else b"",
    )
    assert status == 422
    assert payload["code"] == "configuration_incomplete"


def test_workspace_requires_tenant_and_actor() -> None:
    status, payload = _plane(AuthorizationConnection()).handle(  # type: ignore[arg-type]
        "GET",
        "/v1/workspace",
        {"x-buyer-ops-token": "token", "x-buyer-ops-actor": "actor-1"},
        b"",
    )
    assert status == 403
    assert payload["code"] == "authority_denied"


@pytest.mark.parametrize("route", ["/v1/journeys", "/v1/journeys/journey-1"])
def test_journey_projection_routes_fail_closed_until_derivation_is_published(
    route: str,
) -> None:
    plane = _plane(AuthorizationConnection())  # type: ignore[arg-type]
    plane._require_actor = lambda tenant_id, actor_id: None  # type: ignore[method-assign]
    status, payload = plane.handle(
        "GET",
        route,
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
            "x-buyer-ops-actor": "actor-1",
        },
        b"",
    )
    assert status == 422
    assert payload["code"] == "configuration_incomplete"


def test_tenancies_are_empty_without_actor_authorization() -> None:
    status, payload = _plane(AuthorizationConnection()).handle(  # type: ignore[arg-type]
        "GET",
        "/v1/actors/tenancies",
        {"x-buyer-ops-token": "token", "x-buyer-ops-actor": "actor-1"},
        b"",
    )
    assert status == 200
    assert payload == {"tenancies": []}


def test_tenancies_require_authenticated_actor() -> None:
    status, payload = _plane(AuthorizationConnection()).handle(  # type: ignore[arg-type]
        "GET",
        "/v1/actors/tenancies",
        {"x-buyer-ops-token": "token"},
        b"",
    )
    assert status == 401
    assert payload["code"] == "authentication_required"


def test_operator_policy_post_fails_closed_without_owner_admission_contract() -> None:
    policy = {
        "message_type": "operator_policy",
        "schema_version": "operator-surface/1.1.0",
        "policy_id": "policy-1",
        "tenant_id": "tenant-1",
        "record_version": 1,
        "effective_from": "2030-01-01T00:00:00Z",
        "status": "active",
        "command_rules": [
            {
                "command_type": "request_reconciliation",
                "action_class": "request_reconciliation",
                "target_record_types": ["EffectAttempt"],
                "actor_types": ["license_holder"],
            }
        ],
        "evidence_refs": [
            {
                "record_id": "evidence-policy-1",
                "record_type": "Evidence",
                "version": 1,
                "digest": "sha256:" + "a" * 64,
                "captured_at": "2030-01-01T00:00:00Z",
            }
        ],
    }
    plane = _plane(AuthorizationConnection())  # type: ignore[arg-type]
    plane._require_actor = lambda tenant_id, actor_id: None  # type: ignore[method-assign]
    status, payload = plane.handle(
        "POST",
        "/v1/operator-policies",
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
            "x-buyer-ops-actor": "actor-1",
        },
        json.dumps(policy).encode(),
    )
    assert status == 422
    assert payload["code"] == "configuration_incomplete"


def test_actor_authorization_post_fails_closed_without_owner_admission_contract() -> None:
    plane = _plane(AuthorizationConnection())  # type: ignore[arg-type]
    plane._require_actor = lambda tenant_id, actor_id: None  # type: ignore[method-assign]
    status, payload = plane.handle(
        "POST",
        "/v1/actor-authorizations",
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
            "x-buyer-ops-actor": "actor-1",
        },
        b"{}",
    )
    assert status == 422
    assert payload["code"] == "configuration_incomplete"


def test_operator_policy_post_requires_authenticated_actor() -> None:
    status, payload = _plane(AuthorizationConnection()).handle(  # type: ignore[arg-type]
        "POST",
        "/v1/operator-policies",
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
        },
        b"{}",
    )
    assert status == 401
    assert payload["code"] == "authentication_required"


def test_actor_authorization_post_requires_authenticated_actor() -> None:
    status, payload = _plane(AuthorizationConnection()).handle(  # type: ignore[arg-type]
        "POST",
        "/v1/actor-authorizations",
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
        },
        b"{}",
    )
    assert status == 401
    assert payload["code"] == "authentication_required"


@pytest.mark.parametrize(
    "route",
    ["/v1/connectors", "/v1/activation", "/v1/canonical/record-1"],
)
def test_operator_read_routes_require_authenticated_actor(route: str) -> None:
    status, payload = _plane(AuthorizationConnection()).handle(  # type: ignore[arg-type]
        "GET",
        route,
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
        },
        b"",
    )
    assert status == 401
    assert payload["code"] == "authentication_required"


@pytest.mark.parametrize(
    ("method", "route"),
    [
        ("GET", "/v1/connectors"),
        ("GET", "/v1/activation"),
        ("GET", "/v1/canonical/record-1"),
        ("POST", "/v1/canonical"),
        ("POST", "/v1/habitat/evaluate-authority"),
        ("POST", "/v1/habitat/admit-event"),
        ("POST", "/v1/actor-authorizations"),
        ("POST", "/v1/operator-policies"),
        ("POST", "/v1/activation/evidence"),
        ("POST", "/v1/activation/decisions"),
    ],
)
def test_operator_routes_reject_actor_without_current_tenancy(method: str, route: str) -> None:
    status, payload = _plane(AuthorizationConnection()).handle(  # type: ignore[arg-type]
        method,
        route,
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
            "x-buyer-ops-actor": "actor-1",
        },
        b"{}" if method == "POST" else b"",
    )
    assert status == 403
    assert payload["code"] == "authority_denied"


@pytest.mark.parametrize(
    "route",
    [
        "/v1/activation/evidence",
        "/v1/activation/decisions",
        "/v1/habitat/evaluate-authority",
        "/v1/habitat/admit-event",
    ],
)
def test_authority_routes_require_authenticated_actor(route: str) -> None:
    status, payload = _plane(AuthorizationConnection()).handle(  # type: ignore[arg-type]
        "POST",
        route,
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
        },
        b"{}",
    )
    assert status == 401
    assert payload["code"] == "authentication_required"


def test_ingress_envelope_fails_closed_without_provider_configuration() -> None:
    plane = _plane(Connection())
    plane._require_actor = lambda tenant_id, actor_id: None  # type: ignore[method-assign]

    status, payload = plane.handle(
        "POST",
        "/v1/ingress/envelope",
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
            "x-buyer-ops-actor": "actor-1",
        },
        b"{}",
    )

    assert status == 422
    assert payload["code"] == "configuration_incomplete"


def test_ingress_envelope_uses_deployment_supplied_provider_runtime() -> None:
    seen_tenants: list[str] = []

    class _RejectingAuthenticator:
        def authenticate(self, tenant_id: str, envelope: InboundEnvelope) -> bool:
            seen_tenants.append(tenant_id)
            return False

    class _NeverArtifacts:
        def verify_payload(self, tenant_id: str, artifact_id: str, digest: str) -> bool:
            raise AssertionError("artifact verification must not follow authentication denial")

    class _NeverCapture:
        def after_ingress(
            self,
            envelope: InboundEnvelope,
            identity: dict[str, Any],
            registered: RegisteredInboundEvent,
            *,
            display_name: str,
        ) -> dict[str, Any]:
            raise AssertionError("capture must not follow authentication denial")

    class _RuntimeFactory:
        def __call__(
            self, *, connection: RepositoryConnection, tenant_id: str
        ) -> IngressProviderRuntime:
            del connection, tenant_id
            return IngressProviderRuntime(
                authenticator=_RejectingAuthenticator(),
                artifacts=_NeverArtifacts(),
                capture=_NeverCapture(),
            )

    plane = _plane(
        Connection(),
        ingress_provider_runtime_factory=_RuntimeFactory(),
    )

    status, payload = plane.handle(
        "POST",
        "/v1/ingress/envelope",
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
        },
        json.dumps(
            {"envelope": _envelope().to_mapping(), "identity": _message_identity()}
        ).encode(),
    )

    assert status == 403
    assert payload["code"] == "ingress_authentication_failed"
    assert seen_tenants == ["tenant-1"]


@pytest.mark.parametrize(
    ("code", "expected_status"),
    [
        ("configuration_incomplete", 422),
        ("validation_failed", 422),
        ("version_conflict", 409),
        ("authority_denied", 403),
        ("connector_revoked", 403),
    ],
)
def test_connector_http_errors_preserve_typed_outcomes(code: str, expected_status: int) -> None:
    plane = _plane(Connection())
    plane._require_actor = lambda tenant_id, actor_id: None  # type: ignore[method-assign]

    def _deny(tenant_id: str, request: dict[str, Any], permit: str) -> dict[str, Any]:
        del tenant_id, request, permit
        raise ConnectorDenied(code, "connector denial detail")

    plane._invoke = _deny  # type: ignore[method-assign]
    status, payload = plane.handle(
        "POST",
        "/v1/connectors/invoke",
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
            "x-buyer-ops-actor": "actor-1",
            "x-buyer-ops-permit": "sha256:" + "b" * 64,
        },
        b"{}",
    )

    assert status == expected_status
    assert payload == {"code": code, "detail": "connector denial detail"}


@pytest.mark.parametrize(
    ("route", "request_payload"),
    [
        (
            "/v1/cognition/metered",
            {"connectorId": "openai.api", "apiKey": "sk-test-metered-key"},
        ),
        (
            "/v1/cognition/local",
            {"baseUrl": "http://model-runtime", "modelId": "owner-selected-model"},
        ),
        ("/v1/cognition/oauth/poll", {"sessionId": "oauth-session-1"}),
    ],
)
def test_cognition_binding_routes_fail_closed_without_identity_admission(
    route: str, request_payload: dict[str, Any]
) -> None:
    plane = _plane(Connection())
    plane._require_actor = lambda tenant_id, actor_id: None  # type: ignore[method-assign]

    status, payload = plane.handle(
        "POST",
        route,
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
            "x-buyer-ops-actor": "actor-1",
        },
        json.dumps(request_payload).encode(),
    )

    assert status == 422
    assert payload["code"] == "configuration_incomplete"
    assert payload["detail"] == "credential_identity_admission_unavailable"


def test_invalid_operator_command_returns_typed_operator_error() -> None:
    connection = AuthorizationConnection()
    connection.cursor_instance.rows = [
        (_grant(effectiveAt="2020-01-01T00:00:00Z", expiresAt="2030-02-01T00:00:00Z"),)
    ]
    status, payload = _plane(connection).handle(  # type: ignore[arg-type]
        "POST",
        "/v1/commands",
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
            "x-buyer-ops-actor": "actor-1",
        },
        b"{}",
    )
    assert status == 422
    assert payload["message_type"] == "operator_error"
    assert payload["schema_version"] == "operator-surface/1.1.0"
    assert payload["code"] == "validation_failed"
    validate_record(payload, "operator_surface")


def test_malformed_operator_command_json_returns_typed_operator_error() -> None:
    status, payload = _plane(AuthorizationConnection()).handle(  # type: ignore[arg-type]
        "POST",
        "/v1/commands",
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
            "x-buyer-ops-actor": "actor-1",
        },
        b"{",
    )
    assert status == 422
    assert payload["message_type"] == "operator_error"
    assert payload["code"] == "validation_failed"
    validate_record(payload, "operator_surface")


def test_platform_oauth_client_save_requires_actor_tenancy_and_owner_contract() -> None:
    plane = _plane(AuthorizationConnection())
    status, payload = plane.handle(
        "POST",
        "/v1/platform/oauth-clients",
        {"x-buyer-ops-token": "token"},
        json.dumps({"issuer": "google", "clientId": "id", "clientSecret": "secret"}).encode(),
    )
    assert status == 401
    assert payload["code"] == "authentication_required"

    status, payload = plane.handle(
        "POST",
        "/v1/platform/oauth-clients",
        {"x-buyer-ops-token": "token", "x-buyer-ops-actor": "actor-1"},
        json.dumps({"issuer": "google", "clientId": "id", "clientSecret": "secret"}).encode(),
    )
    assert status == 403
    assert payload["code"] == "authority_denied"

    class _Store:
        def save(self, **kwargs: object) -> dict[str, str]:
            del kwargs
            raise AssertionError("unpublished HTTP authority reached platform OAuth storage")

    plane._tenancies = lambda actor_id: [{"tenant_id": "1"}]  # type: ignore[method-assign]
    plane._platform_oauth = lambda connection: _Store()  # type: ignore[method-assign]
    status, payload = plane.handle(
        "POST",
        "/v1/platform/oauth-clients",
        {"x-buyer-ops-token": "token", "x-buyer-ops-actor": "actor-1"},
        json.dumps(
            {"issuer": "google", "clientId": "google-client", "clientSecret": "google-secret"}
        ).encode(),
    )
    assert status == 422
    assert payload["code"] == "configuration_incomplete"


def test_platform_oauth_secret_material_has_no_http_readback_surface() -> None:
    plane = _plane(AuthorizationConnection())
    status, payload = plane.handle(
        "GET",
        "/v1/platform/oauth-clients/material",
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
            "x-buyer-ops-actor": "actor-1",
        },
        b"",
    )
    assert status == 404
    assert payload["code"] == "validation_failed"


def test_connector_oauth_completion_requires_authenticated_actor() -> None:
    status, payload = _plane(AuthorizationConnection()).handle(  # type: ignore[arg-type]
        "POST",
        "/v1/connectors/oauth/complete",
        {"x-buyer-ops-token": "token"},
        json.dumps({"state": "untrusted", "code": "untrusted"}).encode(),
    )
    assert status == 401
    assert payload["code"] == "authentication_required"


def test_connector_oauth_http_routes_fail_closed_without_admission_contract() -> None:
    plane = _plane(Connection())
    plane._require_actor = lambda tenant_id, actor_id: None  # type: ignore[method-assign]
    headers = {
        "x-buyer-ops-token": "token",
        "x-buyer-ops-tenant": "tenant-1",
        "x-buyer-ops-actor": "actor-1",
    }

    start_status, start_payload = plane.handle(
        "POST",
        "/v1/connectors/oauth/start",
        headers,
        json.dumps(
            {
                "connectorId": "google.workspace",
                "redirectUri": "http://127.0.0.1/api/connectors/callback",
            }
        ).encode(),
    )

    expires = int(time.time()) + 600
    unsigned_state = f"tenant-1.session-existing.{expires}"
    signature = hmac.new(b"x" * 32, unsigned_state.encode(), hashlib.sha256).hexdigest()
    complete_status, complete_payload = plane.handle(
        "POST",
        "/v1/connectors/oauth/complete",
        headers,
        json.dumps({"state": f"{unsigned_state}.{signature}", "code": "provider-code"}).encode(),
    )

    assert start_status == 422
    assert start_payload["code"] == "configuration_incomplete"
    assert complete_status == 422
    assert complete_payload["code"] == "configuration_incomplete"


def test_platform_oauth_client_metadata_requires_authenticated_actor() -> None:
    status, payload = _plane(AuthorizationConnection()).handle(  # type: ignore[arg-type]
        "GET",
        "/v1/platform/oauth-clients",
        {"x-buyer-ops-token": "token"},
        b"",
    )
    assert status == 401
    assert payload["code"] == "authentication_required"


def test_activation_readback_is_empty_until_signed_evidence_exists() -> None:
    plane = _plane(AuthorizationConnection())  # type: ignore[arg-type]
    plane._require_actor = lambda tenant_id, actor_id: None  # type: ignore[method-assign]
    status, payload = plane.handle(
        "GET",
        "/v1/activation",
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
            "x-buyer-ops-actor": "actor-1",
        },
        b"",
    )
    assert status == 200
    assert payload == {"decisions": []}


def test_canonical_post_does_not_admit_even_structurally_invalid_payload() -> None:
    plane = _plane(Connection())
    plane._require_actor = lambda tenant_id, actor_id: None  # type: ignore[method-assign]
    status, payload = plane.handle(
        "POST",
        "/v1/canonical",
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
            "x-buyer-ops-actor": "actor-1",
        },
        json.dumps({"recordType": "LicenseHolder"}).encode(),
    )
    assert status == 422
    assert payload["code"] == "configuration_incomplete"
