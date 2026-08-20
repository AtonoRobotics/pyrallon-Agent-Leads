from datetime import UTC, datetime

import pytest

from buyer_ops_contracts.errors import ContractViolation
from buyer_ops_contracts.habitat import (
    HabitatKernel,
    HabitatState,
    PolicyDisposition,
    validate_effect_intent,
)


def _intent() -> dict:
    return {
        "schema_version": "effect-intent/1.0.0",
        "intent_id": "intent-1",
        "tenant_id": "tenant-1",
        "principal_id": "principal-1",
        "buyer_journey_id": "journey-1",
        "workflow_id": "workflow-1",
        "activity_id": "activity-1",
        "action_class": "send_message",
        "connector_binding_id": "connector-1",
        "target_resource": {
            "resource_type": "conversation",
            "resource_id": "conversation-1",
            "version": 4,
        },
        "recipient": {"recipient_type": "person", "recipient_id": "person-1"},
        "payload_digest": "sha256:" + "a" * 64,
        "canonical_version_vector": {"conversation-1": 4, "person-1": 2},
        "proposal_id": "proposal-1",
        "proposal_expires_at": "2030-01-01T00:05:00Z",
        "idempotency_key": "send:conversation-1:4",
        "purpose": "buyer_consultation",
        "trace_id": "trace-1",
        "evidence_correlation_ids": ["evidence-1"],
    }


def test_effect_intent_rejects_unknown_schema_and_cross_tenant_before_admission() -> None:
    unknown = _intent()
    unknown["schema_version"] = "effect-intent/9.0.0"
    with pytest.raises(ContractViolation, match="STRUCTURAL_SCHEMA"):
        validate_effect_intent(unknown, expected_tenant_id="tenant-1")

    cross_tenant = _intent()
    with pytest.raises(ContractViolation, match="TENANT_ADMISSION"):
        validate_effect_intent(cross_tenant, expected_tenant_id="tenant-2")


def test_effect_intent_rejects_expired_proposal_deterministically() -> None:
    with pytest.raises(ContractViolation, match="PROPOSAL_EXPIRED"):
        validate_effect_intent(
            _intent(),
            expected_tenant_id="tenant-1",
            evaluated_at=datetime(2030, 1, 1, 0, 5, tzinfo=UTC),
        )


def test_effect_intent_requires_target_version_in_canonical_vector() -> None:
    intent = _intent()
    intent["canonical_version_vector"]["conversation-1"] = 3
    with pytest.raises(ContractViolation, match="TARGET_VERSION_BINDING"):
        validate_effect_intent(intent, expected_tenant_id="tenant-1")

    missing = _intent()
    del missing["canonical_version_vector"]["conversation-1"]
    with pytest.raises(ContractViolation, match="TARGET_VERSION_BINDING"):
        validate_effect_intent(missing, expected_tenant_id="tenant-1")


class _StateReader:
    def __init__(self, state: HabitatState) -> None:
        self.state = state

    def load_current(self, intent: dict) -> HabitatState:
        return self.state


class _Policy:
    def __init__(self, disposition: str = "allowed") -> None:
        self.disposition = disposition

    def evaluate(
        self, intent: dict, state: HabitatState, evaluated_at: datetime
    ) -> PolicyDisposition:
        return PolicyDisposition(
            disposition=self.disposition,
            policy_id="tenant-effect-policy",
            policy_version="7",
        )


def _matching_records() -> dict:
    return {
        "conversation-1": {"id": "conversation-1", "tenantId": "tenant-1", "version": 4},
        "person-1": {"id": "person-1", "tenantId": "tenant-1", "version": 2},
    }


def _principal() -> dict:
    return {
        "id": "principal-1",
        "tenantId": "tenant-1",
        "recordType": "ServicePrincipal",
        "status": "active",
        "principalState": "active",
    }


def _authorization() -> dict:
    return {
        "status": "active",
        "authorizationState": "active",
        "granteeId": "principal-1",
        "actionClass": "send_message",
        "resourceType": "conversation",
        "resourceId": "conversation-1",
        "grantedAt": "2029-12-31T00:00:00Z",
        "expiresAt": "2030-01-02T00:00:00Z",
    }


def _workflow_reference() -> dict:
    return {
        "status": "active",
        "workflowId": "workflow-1",
        "subjectId": "journey-1",
        "executionState": "running",
    }


def test_habitat_denies_non_current_workflow_ownership() -> None:
    state = HabitatState(
        records=_matching_records(),
        principal=_principal(),
        authorization=_authorization(),
        workflow_reference={
            "status": "active",
            "workflowId": "workflow-other",
            "subjectId": "journey-1",
            "executionState": "running",
        },
    )
    decision = HabitatKernel(_StateReader(state), _Policy()).admit(
        _intent(),
        expected_tenant_id="tenant-1",
        evaluated_at=datetime(2030, 1, 1, 0, 4, tzinfo=UTC),
    )
    assert decision.reason == "concurrency_conflict"


def test_habitat_denies_when_current_canonical_version_changed() -> None:
    state = HabitatState(
        records={
            "conversation-1": {"id": "conversation-1", "tenantId": "tenant-1", "version": 5},
            "person-1": {"id": "person-1", "tenantId": "tenant-1", "version": 2},
        }
    )
    decision = HabitatKernel(_StateReader(state), _Policy()).admit(
        _intent(),
        expected_tenant_id="tenant-1",
        evaluated_at=datetime(2030, 1, 1, 0, 4, tzinfo=UTC),
    )
    assert decision.allowed is False
    assert decision.reason == "canonical_version_conflict"


def test_habitat_denies_missing_current_principal_identity() -> None:
    state = HabitatState(
        records={
            "conversation-1": {"id": "conversation-1", "tenantId": "tenant-1", "version": 4},
            "person-1": {"id": "person-1", "tenantId": "tenant-1", "version": 2},
        }
    )
    decision = HabitatKernel(_StateReader(state), _Policy()).admit(
        _intent(),
        expected_tenant_id="tenant-1",
        evaluated_at=datetime(2030, 1, 1, 0, 4, tzinfo=UTC),
    )
    assert decision.reason == "identity_invalid"


def test_habitat_denies_when_scoped_authority_is_missing() -> None:
    decision = HabitatKernel(
        _StateReader(HabitatState(records=_matching_records(), principal=_principal()))
    ).admit(
        _intent(),
        expected_tenant_id="tenant-1",
        evaluated_at=datetime(2030, 1, 1, 0, 4, tzinfo=UTC),
    )
    assert decision.reason == "authority_missing"


def test_habitat_denies_when_approved_payload_changed() -> None:
    intent = _intent()
    intent.update(
        approval_ref="approval-1",
        approved_digest="sha256:" + "b" * 64,
    )
    approval = {
        "id": "approval-1",
        "status": "active",
        "decision": "approved",
        "actionClass": "send_message",
        "actionIntentId": "intent-1",
        "payloadDigest": "sha256:" + "b" * 64,
        "expiresAt": "2030-01-02T00:00:00Z",
    }
    state = HabitatState(
        records=_matching_records(),
        principal=_principal(),
        authorization=_authorization(),
        approval=approval,
        workflow_reference=_workflow_reference(),
    )
    decision = HabitatKernel(_StateReader(state), _Policy()).admit(
        intent,
        expected_tenant_id="tenant-1",
        evaluated_at=datetime(2030, 1, 1, 0, 4, tzinfo=UTC),
    )
    assert decision.reason == "payload_changed"


def test_habitat_active_suppression_dominates_current_consent() -> None:
    connector = {
        "connectorBindingId": "connector-1",
        "principalId": "principal-1",
        "state": "active",
        "actionClasses": ["send_message"],
        "channel": "email",
        "requiresConsent": True,
    }
    consent = {
        "personId": "person-1",
        "principalId": "principal-1",
        "channel": "email",
        "purpose": "buyer_consultation",
        "validityState": "active",
        "grantedAt": "2029-12-31T00:00:00Z",
    }
    suppression = {
        "subjectId": "person-1",
        "validityState": "active",
        "suppressedAt": "2030-01-01T00:03:00Z",
    }
    state = HabitatState(
        records=_matching_records(),
        principal=_principal(),
        authorization=_authorization(),
        workflow_reference=_workflow_reference(),
        connector_grant=connector,
        consent=consent,
        suppression=suppression,
    )
    decision = HabitatKernel(_StateReader(state), _Policy()).admit(
        _intent(),
        expected_tenant_id="tenant-1",
        evaluated_at=datetime(2030, 1, 1, 0, 4, tzinfo=UTC),
    )
    assert decision.reason == "consent_denied"


def test_habitat_fails_closed_without_current_policy_evaluator() -> None:
    state = HabitatState(
        records=_matching_records(),
        principal=_principal(),
        authorization=_authorization(),
        workflow_reference=_workflow_reference(),
        connector_grant={
            "connectorBindingId": "connector-1",
            "principalId": "principal-1",
            "state": "active",
            "actionClasses": ["send_message"],
            "requiresConsent": False,
        },
    )
    decision = HabitatKernel(_StateReader(state)).admit(
        _intent(),
        expected_tenant_id="tenant-1",
        evaluated_at=datetime(2030, 1, 1, 0, 4, tzinfo=UTC),
    )
    assert decision.reason == "policy_denied"


def test_habitat_hard_stops_residential_showing_without_qualification() -> None:
    intent = _intent()
    intent["action_class"] = "residential_showing"
    authorization = _authorization()
    authorization["actionClass"] = "residential_showing"
    state = HabitatState(
        records=_matching_records(),
        principal=_principal(),
        authorization=authorization,
        workflow_reference=_workflow_reference(),
        connector_grant={
            "connectorBindingId": "connector-1",
            "principalId": "principal-1",
            "state": "active",
            "actionClasses": ["residential_showing"],
            "requiresConsent": False,
        },
    )
    decision = HabitatKernel(_StateReader(state), _Policy()).admit(
        intent,
        expected_tenant_id="tenant-1",
        evaluated_at=datetime(2030, 1, 1, 0, 4, tzinfo=UTC),
    )
    assert decision.reason == "representation_conflict"


def test_habitat_rereads_exact_current_agreement_before_showing() -> None:
    intent = _intent()
    intent["action_class"] = "residential_showing"
    authorization = _authorization()
    authorization["actionClass"] = "residential_showing"
    state = HabitatState(
        records=_matching_records(),
        principal=_principal(),
        authorization=authorization,
        workflow_reference=_workflow_reference(),
        connector_grant={
            "connectorBindingId": "connector-1",
            "principalId": "principal-1",
            "state": "active",
            "actionClasses": ["residential_showing"],
            "requiresConsent": False,
        },
        agreement_qualification={
            "status": "active",
            "result": "qualified",
            "actionType": "residential_showing",
            "actionIntentId": "intent-1",
            "actionPayloadDigest": "sha256:" + "a" * 64,
            "expiresAt": "2030-01-02T00:00:00Z",
            "agreementId": "agreement-1",
            "agreementVersion": 3,
        },
        agreement={
            "id": "agreement-1",
            "version": 3,
            "status": "active",
            "executionState": "effective",
            "effectiveAt": "2029-12-01T00:00:00Z",
            "terminatesAt": "2030-01-01T00:03:00Z",
        },
    )
    decision = HabitatKernel(_StateReader(state), _Policy()).admit(
        intent,
        expected_tenant_id="tenant-1",
        evaluated_at=datetime(2030, 1, 1, 0, 4, tzinfo=UTC),
    )
    assert decision.reason == "representation_conflict"


def test_habitat_rejects_unknown_iabs_delivery_at_effect_time() -> None:
    intent = _intent()
    intent["action_class"] = "residential_showing"
    authorization = _authorization()
    authorization["actionClass"] = "residential_showing"
    state = HabitatState(
        records=_matching_records(),
        principal=_principal(),
        authorization=authorization,
        workflow_reference=_workflow_reference(),
        connector_grant={
            "connectorBindingId": "connector-1",
            "principalId": "principal-1",
            "state": "active",
            "actionClasses": ["residential_showing"],
            "requiresConsent": False,
        },
        agreement_qualification={
            "status": "active",
            "result": "qualified",
            "actionType": "residential_showing",
            "actionIntentId": "intent-1",
            "actionPayloadDigest": "sha256:" + "a" * 64,
            "expiresAt": "2030-01-02T00:00:00Z",
            "iabsDeliveryId": "iabs-1",
        },
        iabs_delivery={
            "id": "iabs-1",
            "status": "active",
            "validityState": "delivery_unknown",
        },
    )
    decision = HabitatKernel(_StateReader(state), _Policy()).admit(
        intent,
        expected_tenant_id="tenant-1",
        evaluated_at=datetime(2030, 1, 1, 0, 4, tzinfo=UTC),
    )
    assert decision.reason == "representation_conflict"


def test_habitat_policy_can_require_exact_approval() -> None:
    state = HabitatState(
        records=_matching_records(),
        principal=_principal(),
        authorization=_authorization(),
        workflow_reference=_workflow_reference(),
        connector_grant={
            "connectorBindingId": "connector-1",
            "principalId": "principal-1",
            "state": "active",
            "actionClasses": ["send_message"],
            "requiresConsent": False,
        },
    )
    decision = HabitatKernel(_StateReader(state), _Policy("approval_required")).admit(
        _intent(),
        expected_tenant_id="tenant-1",
        evaluated_at=datetime(2030, 1, 1, 0, 4, tzinfo=UTC),
    )
    assert decision.reason == "approval_required"
    assert decision.policy_version == "7"


def test_habitat_rejects_superseded_exact_approval() -> None:
    intent = _intent()
    intent.update(approval_ref="approval-1", approved_digest=intent["payload_digest"])
    state = HabitatState(
        records=_matching_records(),
        principal=_principal(),
        authorization=_authorization(),
        workflow_reference=_workflow_reference(),
        approval={
            "id": "approval-1",
            "status": "superseded",
            "decision": "approved",
            "actionClass": "send_message",
            "actionIntentId": "intent-1",
            "payloadDigest": intent["payload_digest"],
            "expiresAt": "2030-01-02T00:00:00Z",
        },
        connector_grant={
            "connectorBindingId": "connector-1",
            "principalId": "principal-1",
            "state": "active",
            "actionClasses": ["send_message"],
            "requiresConsent": False,
        },
    )
    decision = HabitatKernel(_StateReader(state), _Policy("approval_required")).admit(
        intent,
        expected_tenant_id="tenant-1",
        evaluated_at=datetime(2030, 1, 1, 0, 4, tzinfo=UTC),
    )
    assert decision.reason == "approval_required"
