from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_actor_authorization import _Connection as AuthorizationConnection
from test_actor_authorization import _grant
from test_canonical_repository import Connection, _license_holder

from buyer_ops_contracts.control_plane import ControlPlane
from buyer_ops_contracts.structural import validate_record


def _plane(connection: object) -> ControlPlane:
    public = Ed25519PrivateKey.generate().public_key()
    plane = ControlPlane(
        "postgresql://unused",
        permit_secret=b"x" * 32,
        control_token="token",
        release_public_keys={"health-test": public},
        gate_registry_path=Path("PRODUCTION-GATE-REGISTRY.yaml"),
    )
    plane._connection = lambda: connection  # type: ignore[method-assign]
    return plane


def test_canonical_post_admits_license_holder() -> None:
    connection = Connection()
    connection.cursor_instance.references["person-agent-1"] = {
        "recordType": "Person",
        "tenantId": "tenant-1",
    }
    connection.cursor_instance.references["endpoint-agent-1"] = {
        "recordType": "ContactEndpoint",
        "tenantId": "tenant-1",
    }
    status, payload = _plane(connection).handle(
        "POST",
        "/v1/canonical",
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
        },
        json.dumps(_license_holder()).encode(),
    )
    assert status == 200
    assert payload["id"] == "holder-1"
    assert payload["recordType"] == "LicenseHolder"


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


def test_workspace_journey_unquotes_colon_ids() -> None:
    plane = _plane(Connection())
    seen: dict[str, str] = {}

    def _journey(tenant_id: str, actor_id: str, journey_id: str) -> dict[str, str]:
        del tenant_id, actor_id
        seen["id"] = journey_id
        return {"id": journey_id}

    plane._workspace_journey = _journey  # type: ignore[method-assign]
    status, payload = plane.handle(
        "GET",
        "/v1/workspace/journeys/journey%3A1111-2222",
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


def test_setup_tenant_does_not_require_tenant_header() -> None:
    class _Boom:
        def close(self) -> None:
            return None

    plane = _plane(_Boom())

    def _raise() -> object:
        raise RuntimeError("bootstrap should be invoked")

    plane._setup_tenant = lambda actor_id, payload: {  # type: ignore[method-assign]
        "tenantId": payload["tenantId"],
        "actorId": actor_id,
        "pendingConnectors": ["twilio.sms"],
    }
    del _raise
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
    assert status == 200
    assert payload["tenantId"] == "brokerage-live-1"
    assert payload["actorId"] == "actor-1"


def test_workspace_requires_tenant_and_actor() -> None:
    status, payload = _plane(AuthorizationConnection()).handle(  # type: ignore[arg-type]
        "GET",
        "/v1/workspace",
        {"x-buyer-ops-token": "token", "x-buyer-ops-actor": "actor-1"},
        b"",
    )
    assert status == 403
    assert payload["code"] == "authority_denied"


def test_tenancies_are_empty_without_actor_authorization() -> None:
    status, payload = _plane(AuthorizationConnection()).handle(  # type: ignore[arg-type]
        "GET",
        "/v1/actors/tenancies",
        {"x-buyer-ops-token": "token", "x-buyer-ops-actor": "actor-1"},
        b"",
    )
    assert status == 200
    assert payload == {"tenancies": []}


def test_operator_policy_post_admits_published_policy() -> None:
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
    status, payload = _plane(AuthorizationConnection()).handle(  # type: ignore[arg-type]
        "POST",
        "/v1/operator-policies",
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
        },
        json.dumps(policy).encode(),
    )
    assert status == 200
    assert payload["policy_id"] == "policy-1"


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


def test_platform_oauth_client_save_requires_actor_and_tenancy() -> None:
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
            return {
                "issuer": str(kwargs["issuer"]),
                "clientId": str(kwargs["client_id"]),
                "configured": "true",
            }

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
    assert status == 200
    assert payload == {"issuer": "google", "clientId": "google-client", "configured": "true"}


def test_activation_readback_is_empty_until_signed_evidence_exists() -> None:
    status, payload = _plane(AuthorizationConnection()).handle(  # type: ignore[arg-type]
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


def test_canonical_post_rejects_invalid_ontology_record() -> None:
    status, payload = _plane(Connection()).handle(
        "POST",
        "/v1/canonical",
        {
            "x-buyer-ops-token": "token",
            "x-buyer-ops-tenant": "tenant-1",
        },
        json.dumps({"recordType": "LicenseHolder"}).encode(),
    )
    assert status == 422
    assert payload["code"] == "validation_failed"
