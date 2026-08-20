from __future__ import annotations

from datetime import UTC, datetime

import pytest

from buyer_ops_contracts.actor_authorization import (
    ActorTenantAuthorizationRepository,
    admit_published_record,
    authorize_operator_command,
)
from buyer_ops_contracts.authority_activation_fair_housing import (
    validate_authority_activation_fair_housing_semantics,
)
from buyer_ops_contracts.canonical_repository import VersionConflict
from buyer_ops_contracts.errors import ContractViolation
from buyer_ops_contracts.structural import validate_record


def _grant(**overrides: object) -> dict:
    record = {
        "schemaVersion": "open-025-027/1.0.0",
        "recordType": "ActorTenantAuthorization",
        "tenantId": "tenant-1",
        "recordId": "auth-1",
        "observedAt": "2030-01-01T00:00:00Z",
        "actorId": "actor-1",
        "principalId": "principal-1",
        "role": "agent",
        "allowedCommands": ["request_reconciliation"],
        "recordScopes": ["journey"],
        "policyVersion": "policy-1",
        "authorizationVersion": 1,
        "effectiveAt": "2030-01-01T00:00:00Z",
        "expiresAt": "2030-02-01T00:00:00Z",
        "status": "active",
    }
    record.update(overrides)
    return record


class _Cursor:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.rows: list[tuple[object, ...]] = []
        self.row: tuple[object, ...] | None = None

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        self.statements.append((statement, parameters))

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows

    def fetchone(self) -> tuple[object, ...] | None:
        return self.row


class _Connection:
    def __init__(self) -> None:
        self.cursor_instance = _Cursor()
        self.commits = 0

    def cursor(self) -> _Cursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


def test_expired_actor_authorization_fails_closed() -> None:
    record = _grant(expiresAt="2030-01-01T00:00:00Z")
    validate_record(record, "authority_activation_fair_housing")
    with pytest.raises(ContractViolation, match="AUTHORIZATION_EXPIRED"):
        validate_authority_activation_fair_housing_semantics(
            record, now=datetime(2030, 1, 2, tzinfo=UTC)
        )


def test_expired_and_revoked_grants_are_not_current() -> None:
    connection = _Connection()
    expired = _grant(recordId="auth-expired", expiresAt="2030-01-01T00:00:00Z")
    revoked = _grant(recordId="auth-revoked", status="revoked", revokedAt="2030-01-01T12:00:00Z")
    connection.cursor_instance.rows = [(expired,), (revoked,)]
    listed = ActorTenantAuthorizationRepository(
        connection, tenant_id="tenant-1"
    ).list_current_for_actor("actor-1", now=datetime(2030, 1, 2, tzinfo=UTC))
    assert listed == []
    assert (
        ActorTenantAuthorizationRepository(connection, tenant_id="tenant-1").current(
            "actor-1", now=datetime(2030, 1, 2, tzinfo=UTC)
        )
        is None
    )


def test_current_grant_is_tenant_isolated() -> None:
    connection = _Connection()
    connection.cursor_instance.rows = [
        (_grant(),),
        (_grant(tenantId="tenant-2", recordId="auth-other"),),
    ]
    current = ActorTenantAuthorizationRepository(connection, tenant_id="tenant-1").current(
        "actor-1", now=datetime(2030, 1, 2, tzinfo=UTC)
    )
    assert current is not None
    assert current["tenantId"] == "tenant-1"
    assert current["recordId"] == "auth-1"
    other = ActorTenantAuthorizationRepository(connection, tenant_id="tenant-2").current(
        "actor-1", now=datetime(2030, 1, 2, tzinfo=UTC)
    )
    assert other is not None
    assert other["tenantId"] == "tenant-2"


def test_repository_lists_only_current_active_grants_for_actor() -> None:
    connection = _Connection()
    grant = _grant()
    connection.cursor_instance.rows = [(grant,), (_grant(status="revoked", recordId="auth-2"),)]
    listed = ActorTenantAuthorizationRepository(connection).list_current_for_actor(
        "actor-1", now=datetime(2030, 1, 2, tzinfo=UTC)
    )
    assert [item["recordId"] for item in listed] == ["auth-1"]


def test_repository_save_refuses_non_authorization_record() -> None:
    with pytest.raises(ValueError, match="only ActorTenantAuthorization"):
        ActorTenantAuthorizationRepository(_Connection(), tenant_id="tenant-1").save(
            {
                "schemaVersion": "open-025-027/1.0.0",
                "recordType": "ReleaseActivation",
                "tenantId": "tenant-1",
                "recordId": "activation-1",
                "observedAt": "2030-01-01T00:00:00Z",
                "environment": "production",
                "releaseId": "release-1",
                "buildDigest": "sha256:" + "a" * 64,
                "contractManifestDigest": "sha256:" + "b" * 64,
                "policyVersion": "policy-1",
                "enabledCapabilities": ["email"],
                "requiredGateIds": ["GATE-001"],
                "gateEvidence": [
                    {
                        "gateId": "GATE-001",
                        "applicability": "platform_invariant",
                        "outcome": "pass",
                        "evidenceId": "evidence-1",
                        "evidenceDigest": "sha256:" + "c" * 64,
                        "expiresAt": "2030-02-01T00:00:00Z",
                    }
                ],
                "signerActorId": "actor-1",
                "signature": "sha256:" + "d" * 64,
                "effectiveAt": "2030-01-01T00:00:00Z",
                "expiresAt": "2030-02-01T00:00:00Z",
                "status": "active",
            }
        )


def test_new_actor_authorization_lineage_must_start_at_version_one() -> None:
    connection = _Connection()

    with pytest.raises(VersionConflict, match="start at version 1"):
        ActorTenantAuthorizationRepository(connection, tenant_id="tenant-1").save(
            _grant(authorizationVersion=2),
            now=datetime(2030, 1, 2, tzinfo=UTC),
        )

    assert connection.commits == 0
    assert not any(
        "INSERT INTO actor_tenant_authorization_versions" in statement
        for statement, _ in connection.cursor_instance.statements
    )


def test_actor_authorization_successor_must_increment_current_version() -> None:
    connection = _Connection()
    connection.cursor_instance.row = (1,)

    with pytest.raises(VersionConflict, match="version conflict"):
        ActorTenantAuthorizationRepository(connection, tenant_id="tenant-1").save(
            _grant(authorizationVersion=3),
            now=datetime(2030, 1, 2, tzinfo=UTC),
        )

    assert connection.commits == 0


def test_actor_authorization_exact_next_version_is_admitted() -> None:
    connection = _Connection()
    connection.cursor_instance.row = (1,)
    successor = _grant(authorizationVersion=2, observedAt="2030-01-02T00:00:00Z")

    assert (
        ActorTenantAuthorizationRepository(connection, tenant_id="tenant-1").save(
            successor,
            now=datetime(2030, 1, 2, tzinfo=UTC),
        )
        == successor
    )
    assert connection.commits == 1


def _command() -> dict:
    return {
        "tenant_id": "tenant-1",
        "command_type": "request_reconciliation",
        "target_record_type": "WorkflowReference",
        "authority": {
            "resource_type": "journey",
            "resource_id": "journey-1",
            "authorization_refs": [
                {
                    "record_type": "ActorTenantAuthorization",
                    "record_id": "auth-1",
                    "version": 1,
                    "status": "active",
                }
            ],
            "policy_ref": {"record_id": "policy-1"},
        },
    }


def test_command_authority_is_exactly_tenant_scope_and_version_bound() -> None:
    authorize_operator_command(_grant(), _command(), actor_id="actor-1", tenant_id="tenant-1")
    for changed in (
        _grant(tenantId="tenant-2"),
        _grant(recordScopes=["Approval"]),
        _grant(authorizationVersion=2),
        _grant(policyVersion="policy-2"),
    ):
        with pytest.raises(PermissionError):
            authorize_operator_command(
                changed, _command(), actor_id="actor-1", tenant_id="tenant-1"
            )


def test_admit_published_record_routes_actor_tenant_authorization() -> None:
    connection = _Connection()
    saved = admit_published_record(connection, _grant(), now=datetime(2030, 1, 2, tzinfo=UTC))
    assert saved["recordType"] == "ActorTenantAuthorization"
    assert connection.commits == 1
    assert any(
        "actor_tenant_authorizations_current" in sql
        for sql, _ in connection.cursor_instance.statements
    )


def test_admit_published_record_routes_operator_policy() -> None:
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
    connection = _Connection()
    saved = admit_published_record(connection, policy)
    assert saved["message_type"] == "operator_policy"
    assert saved["policy_id"] == "policy-1"
    assert any(
        "operator_policies_current" in sql for sql, _ in connection.cursor_instance.statements
    )


def test_command_authority_rejects_missing_or_legacy_authorization_reference() -> None:
    command = _command()
    command["authority"]["authorization_refs"][0]["record_type"] = "Authorization"
    with pytest.raises(PermissionError, match="stale or inexact"):
        authorize_operator_command(_grant(), command, actor_id="actor-1", tenant_id="tenant-1")
