from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from buyer_ops_contracts.operator_commands import (
    OperatorCommandError,
    OperatorCommandService,
    command_payload_digest,
)


class _Cursor:
    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        del statement, parameters

    def fetchone(self) -> None:
        return None


class _Connection:
    def cursor(self) -> _Cursor:
        return _Cursor()

    def commit(self) -> None:
        return None


class _Repository:
    def __init__(self, target: dict[str, Any]) -> None:
        self.target = target
        self.save_called = False

    def get(self, record_id: str) -> dict[str, Any] | None:
        if record_id == "authorization-1":
            return {
                "recordType": "Authorization",
                "version": 1,
                "granteeId": "agent-1",
                "authorizationState": "active",
                "status": "active",
                "expiresAt": "2099-01-01T00:00:00Z",
            }
        if record_id == self.target["id"]:
            return self.target
        return None

    def save(self, record: dict[str, Any], *, expected_version: int) -> dict[str, Any]:
        del record, expected_version
        self.save_called = True
        raise AssertionError("design-blocked operator mutation reached canonical save")


def _command(command_type: str, target: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC)
    command: dict[str, Any] = {
        "message_type": "operator_command",
        "schema_version": "operator-surface/1.1.0",
        "command_id": f"command-{command_type}",
        "tenant_id": "tenant-1",
        "journey_id": "journey-1",
        "command_type": command_type,
        "target_record_id": target["id"],
        "target_record_type": target["recordType"],
        "expected_version": target["version"],
        "authority": {
            "actor_id": "agent-1",
            "actor_type": "license_holder",
            "authorization_refs": [
                {
                    "record_id": "authorization-1",
                    "record_type": "ActorTenantAuthorization",
                    "version": 1,
                    "status": "active",
                }
            ],
            "policy_ref": {
                "record_id": "policy-1",
                "record_type": "Policy",
                "version": 1,
                "status": "active",
            },
            "action_class": command_type,
            "resource_type": target["recordType"],
            "resource_id": target["id"],
            "authenticated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "payload_digest": "",
        "idempotency_key": f"key-{command_type}",
        "issued_at": (now - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": "Authorized operator correction.",
    }
    if command_type in {"correct_replace", "correct_invalidate"}:
        command["mutation"] = {
            "kind": "correction",
            "correction_record": {"recordType": "Correction"},
            "corrected_item_update": {"recordType": target["recordType"]},
        }
        if command_type == "correct_replace":
            command["mutation"]["replacement_record"] = {"recordType": target["recordType"]}
    elif command_type == "revoke_authorization":
        command["mutation"] = {
            "kind": "authorization_revocation",
            "authorization_update": {"recordType": "Authorization"},
        }
    elif command_type == "revoke_approval":
        command["mutation"] = {
            "kind": "approval_revocation",
            "prior_approval_update": {"recordType": "Approval"},
            "revoked_approval_record": {"recordType": "Approval"},
        }
    command["payload_digest"] = command_payload_digest(command)
    return command


@pytest.mark.parametrize(
    ("command_type", "record_type"),
    [
        ("correct_replace", "Assertion"),
        ("correct_invalidate", "Assertion"),
        ("revoke_authorization", "Authorization"),
        ("revoke_approval", "Approval"),
    ],
)
def test_incomplete_canonical_mutation_fails_before_repository_write(
    command_type: str, record_type: str
) -> None:
    target = {"id": "target-1", "recordType": record_type, "version": 1}
    repository = _Repository(target)
    service = OperatorCommandService(
        _Connection(),
        repository,  # type: ignore[arg-type]
        tenant_id="tenant-1",
    )

    with pytest.raises(OperatorCommandError) as raised:
        service.dispatch(_command(command_type, target), actor_id="agent-1")

    assert raised.value.code == "validation_failed"
    assert repository.save_called is False
