from __future__ import annotations

from datetime import UTC, datetime

import pytest

from buyer_ops_contracts.operator_contract import operator_payload_digest
from buyer_ops_contracts.operator_surface import OperatorCommandService, OperatorRejected


def _command() -> dict:
    command = {
        "message_type": "operator_command",
        "schema_version": "operator-surface/1.1.0",
        "command_id": "command-1",
        "tenant_id": "tenant-1",
        "journey_id": "journey-1",
        "command_type": "pause_workflow",
        "target_record_id": "workflow-1",
        "target_record_type": "WorkflowReference",
        "expected_version": 4,
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
                "record_id": "policy-3",
                "record_type": "OperatorPolicy",
                "version": 3,
                "status": "active",
            },
            "action_class": "pause_workflow",
            "resource_type": "WorkflowReference",
            "resource_id": "workflow-1",
            "authenticated_at": "2026-08-19T12:00:00Z",
        },
        "payload_digest": "",
        "idempotency_key": "operator-key-1",
        "issued_at": "2026-08-19T12:00:00Z",
        "expires_at": "2026-08-19T12:05:00Z",
        "reason": "Agent requested a temporary pause.",
    }
    command["payload_digest"] = operator_payload_digest(command)
    return command


class _Authority:
    def authorize(self, command: dict) -> bool:
        return command["authority"]["policy_ref"]["record_id"] == "policy-3"


class _Target:
    def current_version(self, command: dict) -> int:
        return 4


class _Executor:
    def execute(self, command: dict) -> dict:
        return {
            "message_type": "operator_command_result",
            "schema_version": "operator-surface/1.1.0",
            "command_id": command["command_id"],
            "tenant_id": command["tenant_id"],
            "status": "applied",
            "decided_at": "2026-08-19T12:00:01Z",
            "decision_evidence_id": "evidence-1",
            "current_version": 5,
            "result_refs": [],
        }


class _Idempotency:
    def __init__(self) -> None:
        self.prior: tuple[str, dict] | None = None

    def lookup(self, tenant_id: str, key: str):
        return self.prior

    def record(self, tenant_id: str, key: str, digest: str, result: dict) -> None:
        self.prior = (digest, result)


def test_operator_command_revalidates_authority_version_and_payload() -> None:
    service = OperatorCommandService(_Authority(), _Target(), _Idempotency(), _Executor())
    result = service.execute(_command(), evaluated_at=_now())
    assert result["status"] == "applied"
    assert result["current_version"] == 5


def test_operator_command_replay_is_duplicate_only_for_same_payload() -> None:
    idempotency = _Idempotency()
    service = OperatorCommandService(_Authority(), _Target(), idempotency, _Executor())
    service.execute(_command(), evaluated_at=_now())
    duplicate = service.execute(_command(), evaluated_at=_now())
    assert duplicate["status"] == "duplicate"

    changed = {**_command(), "payload_digest": "sha256:" + "a" * 64}
    with pytest.raises(OperatorRejected) as raised:
        service.execute(changed, evaluated_at=_now())
    assert raised.value.code == "payload_mismatch"


def test_operator_command_type_cannot_mutate_an_unrelated_record_type() -> None:
    command = {**_command(), "target_record_type": "Approval"}
    with pytest.raises(OperatorRejected) as raised:
        OperatorCommandService(_Authority(), _Target(), _Idempotency(), _Executor()).execute(
            command, evaluated_at=_now()
        )
    assert raised.value.code == "validation_failed"


def _now() -> datetime:
    return datetime(2026, 8, 19, 12, 1, tzinfo=UTC)
