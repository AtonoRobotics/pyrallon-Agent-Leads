from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_actor_authorization import _Connection as AuthorizationConnection
from test_actor_authorization import _grant
from test_canonical_repository import Connection, _license_holder

from buyer_ops_contracts.control_plane import ControlPlane


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
    assert payload == {"tenancies": [{"tenant_id": "tenant-1", "authorization_id": "auth-1"}]}


def test_tenancies_are_empty_without_actor_authorization() -> None:
    status, payload = _plane(AuthorizationConnection()).handle(  # type: ignore[arg-type]
        "GET",
        "/v1/actors/tenancies",
        {"x-buyer-ops-token": "token", "x-buyer-ops-actor": "actor-1"},
        b"",
    )
    assert status == 200
    assert payload == {"tenancies": []}


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
