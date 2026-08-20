from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from buyer_ops_contracts.canonical_habitat import (
    CanonicalLockedHabitatStateReader,
    PlatformPolicyEvaluator,
)
from buyer_ops_contracts.habitat import HabitatState


def test_unconfigured_platform_policy_never_invents_allowed_action_classes() -> None:
    disposition = PlatformPolicyEvaluator().evaluate(
        {"action_class": "send_message"},
        HabitatState(records={}),
        datetime(2026, 8, 19, tzinfo=UTC),
    )
    assert disposition.disposition == "prohibited"
    assert disposition.policy_id == "policy-unavailable"
    assert disposition.policy_version == "unconfigured"


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
        "intent_id": "intent-1",
        "canonical_version_vector": {},
    }


def test_reader_does_not_infer_capability_channel_or_consent_without_resolver() -> None:
    state = CanonicalLockedHabitatStateReader().load_current(_Cursor(), _intent())  # type: ignore[arg-type]
    assert state.connector_grant is None
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
