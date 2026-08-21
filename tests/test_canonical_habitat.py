from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from buyer_ops_contracts.canonical_habitat import (
    CanonicalLockedHabitatStateReader,
    PlatformPolicyEvaluator,
)
from buyer_ops_contracts.habitat import HabitatState, PolicyDisposition


def test_unconfigured_platform_policy_never_invents_allowed_action_classes() -> None:
    disposition = PlatformPolicyEvaluator().evaluate(
        {"action_class": "send_message"},
        HabitatState(records={}),
        datetime(2026, 8, 19, tzinfo=UTC),
    )
    assert disposition.disposition == "prohibited"
    assert disposition.policy_id == "policy-unavailable"
    assert disposition.policy_version == "unconfigured"


def test_platform_policy_evaluator_uses_current_canonical_rule() -> None:
    policy = {
        "recordId": "effect-policy-1",
        "policyId": "effect-policy",
        "policyVersion": "7",
        "tenantId": "tenant-1",
        "status": "current",
        "effectiveFrom": "2026-01-01T00:00:00Z",
        "expiresAt": "2030-01-01T00:00:00Z",
        "selectedRule": {"actionClass": "send_message", "disposition": "allowed"},
    }
    disposition = PlatformPolicyEvaluator().evaluate(
        {"tenant_id": "tenant-1", "action_class": "send_message"},
        HabitatState(records={}, effect_policy=policy),
        datetime(2026, 8, 19, tzinfo=UTC),
    )
    assert disposition == PolicyDisposition("allowed", "effect-policy", "7")


def test_platform_policy_evaluator_rejects_expired_or_invalid_rule() -> None:
    policy = {
        "recordId": "effect-policy-1",
        "policyId": "effect-policy",
        "policyVersion": "7",
        "tenantId": "tenant-1",
        "status": "current",
        "effectiveFrom": "2026-01-01T00:00:00Z",
        "expiresAt": "2026-08-01T00:00:00Z",
        "selectedRule": {"actionClass": "send_message", "disposition": "allowed"},
    }
    disposition = PlatformPolicyEvaluator().evaluate(
        {"tenant_id": "tenant-1", "action_class": "send_message"},
        HabitatState(records={}, effect_policy=policy),
        datetime(2026, 8, 19, tzinfo=UTC),
    )
    assert disposition == PolicyDisposition(
        "prohibited", "policy-invalid", "outside-effective-window"
    )


class _Cursor:
    def __init__(self, *, duplicate_authorization: bool = False) -> None:
        self.params: tuple[Any, ...] = ()
        self.duplicate_authorization = duplicate_authorization

    def execute(self, statement: str, params: tuple[Any, ...]) -> None:
        del statement
        self.params = params

    def fetchone(self) -> tuple[dict[str, Any]] | None:
        record_id = self.params[-1]
        records = {
            "principal-1": {
                "id": "principal-1",
                "recordType": "ServicePrincipal",
                "tenantId": "tenant-1",
            },
            "grant-1": {
                "id": "grant-1",
                "recordType": "ConnectorGrant",
                "tenantId": "tenant-1",
                "grantState": "active",
            },
            "conversation-1": {
                "id": "conversation-1",
                "recordType": "Conversation",
                "tenantId": "tenant-1",
                "version": 4,
            },
        }
        record = records.get(record_id)
        return None if record is None else (record,)

    def fetchall(self) -> list[tuple[dict[str, Any]]]:
        record_type = self.params[-1]
        if record_type == "Authorization":
            record = {
                "id": "authorization-1",
                "granteeId": "principal-1",
                "actionClass": "send_message",
                "resourceType": "Conversation",
                "resourceId": "conversation-1",
            }
            return [(record,), (dict(record),)] if self.duplicate_authorization else [(record,)]
        if record_type == "WorkflowReference":
            return [
                (
                    {
                        "id": "workflow-reference-1",
                        "workflowId": "workflow-1",
                        "subjectId": "journey-1",
                    },
                )
            ]
        return []


def _intent() -> dict[str, Any]:
    return {
        "tenant_id": "tenant-1",
        "principal_id": "principal-1",
        "action_class": "send_message",
        "target_resource": {
            "resource_type": "Conversation",
            "resource_id": "conversation-1",
        },
        "workflow_id": "workflow-1",
        "buyer_journey_id": "journey-1",
        "connector_binding_id": "grant-1",
        "effect_context": {
            "activation_id": "activation-1",
            "activation_digest": "sha256:" + "b" * 64,
            "capability_id": "send",
            "inventory_record_id": "inventory-1",
            "inventory_record_version": 1,
            "inventory_digest": "sha256:" + "c" * 64,
            "constraint_digest": "sha256:" + "d" * 64,
            "grant_id": "grant-1",
            "grant_version": 1,
            "draft_preview_record_id": "proposal-1",
            "draft_preview_record_version": 1,
            "draft_preview_digest": "sha256:" + "e" * 64,
            "delegated_principal_id": "principal-1",
        },
        "intent_id": "intent-1",
        "canonical_version_vector": {},
    }


def test_reader_loads_exact_grant_but_does_not_infer_channel_or_consent() -> None:
    state = CanonicalLockedHabitatStateReader().load_current(_Cursor(), _intent())  # type: ignore[arg-type]
    assert state.connector_grant is not None
    assert state.connector_grant["id"] == "grant-1"
    assert state.consent is None
    assert state.suppression is None


def test_reader_loads_every_canonical_version_vector_record() -> None:
    intent = _intent()
    intent["canonical_version_vector"] = {"conversation-1": 4}
    state = CanonicalLockedHabitatStateReader().load_current(_Cursor(), intent)  # type: ignore[arg-type]
    assert state.records["conversation-1"]["version"] == 4


def test_ambiguous_current_authorization_fails_closed() -> None:
    state = CanonicalLockedHabitatStateReader().load_current(  # type: ignore[arg-type]
        _Cursor(duplicate_authorization=True), _intent()
    )
    assert state.authorization is None
