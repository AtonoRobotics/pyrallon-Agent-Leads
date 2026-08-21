from datetime import UTC, datetime

import pytest

from buyer_ops_contracts.qualification_runtime import QualificationRuntime


def _canonical(record_type: str, record_id: str, **extra: object) -> dict:
    now = "2026-01-01T00:00:00Z"
    return {
        "id": record_id,
        "tenantId": "tenant-1",
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": record_type,
        "version": 1,
        "createdAt": now,
        "updatedAt": now,
        "effectiveFrom": now,
        "createdBy": {"actorType": "system_migration", "actorId": "runtime"},
        "sourceEvidenceIds": ["evidence-1"],
        "status": "active",
        **extra,
    }


def _policy() -> dict:
    return {
        "messageType": "qualification_policy",
        "schemaVersion": "qualification-readiness/1.0.0",
        "tenantId": "tenant-1",
        "policyId": "policy-1",
        "version": 1,
        "owner": {"ownerType": "brokerage", "ownerId": "brokerage-1"},
        "lifecycle": "active",
        "effectiveFrom": "2025-01-01T00:00:00Z",
        "effectiveTo": None,
        "supersedesRecordId": None,
        "criteria": [
            {
                "criterionId": "timing",
                "predicate": "purchase_timing",
                "disposition": "required",
                "acceptedObservationStates": [
                    "asserted",
                    "verified",
                    "buyer_declined",
                    "not_applicable",
                ],
                "maxAgeSeconds": 86400,
                "priority": 1,
                "questionTemplateRef": {
                    "recordId": "q-timing",
                    "recordType": "QuestionTemplate",
                    "version": 1,
                },
                "missingDisposition": "ask",
                "contradictionDisposition": "ask_clarification",
            },
            {
                "criterionId": "represented",
                "predicate": "existing_representation",
                "disposition": "required",
                "acceptedObservationStates": [
                    "asserted",
                    "verified",
                    "buyer_declined",
                    "not_applicable",
                ],
                "maxAgeSeconds": 86400,
                "priority": 2,
                "questionTemplateRef": {
                    "recordId": "q-represented",
                    "recordType": "QuestionTemplate",
                    "version": 1,
                },
                "missingDisposition": "ask",
                "contradictionDisposition": "block_readiness",
            },
        ],
        "questionTieBreak": "priority_then_criterion_id_ascending",
        "readinessAlgorithm": "all_required_resolved_no_blocking_contradiction_zone_and_capacity_v1",
        "serviceZonePolicyRef": {
            "recordId": "zone-policy",
            "recordType": "ServiceZonePolicy",
            "version": 1,
        },
        "capacityPolicyRef": {
            "recordId": "capacity-policy",
            "recordType": "CapacityPolicy",
            "version": 1,
        },
        "urgentEscalationPolicyRef": {
            "recordId": "urgent-policy",
            "recordType": "UrgentEscalationPolicy",
            "version": 1,
        },
        "sourceEvidenceIds": ["evidence-1"],
    }


def _journey() -> dict:
    return _canonical(
        "BuyerJourney",
        "journey-1",
        buyingPartyId="party-1",
        ownerLicenseHolderId="agent-1",
        territory="Austin",
        journeyState="qualifying",
        qualificationState="collecting",
        representationState="not_represented",
    )


def _evaluate(observations: list[dict]) -> dict:
    return QualificationRuntime(
        deriver_principal_id="system",
        implementation_version="1.0.0",
    ).evaluate(
        policy=_policy(),
        journey=_journey(),
        observations=observations,
        evaluated_at=datetime(2026, 1, 1, tzinfo=UTC),
        service_zone_decision_ref={
            "recordId": "zone-decision",
            "recordType": "ServiceZoneDecision",
            "version": 1,
        },
        service_zone_eligible=True,
        capacity_decision_ref={
            "recordId": "capacity-decision",
            "recordType": "CapacityDecision",
            "version": 1,
        },
        capacity_available=True,
        urgent_escalation_refs=[],
    )


def test_progressive_qualification_selects_highest_priority_missing_question() -> None:
    result = _evaluate([])
    assert result["next_question"]["result"] == "ask"
    assert result["next_question"]["criterionId"] == "timing"
    assert result["readiness"]["result"] == "not_ready"


def test_declined_is_distinguished_and_does_not_become_unknown() -> None:
    result = _evaluate(
        [
            _canonical(
                "QualificationObservation",
                "observation-1",
                criterionId="timing",
                epistemicItemId="item-1",
                observationState="buyer_declined",
            ),
            _canonical(
                "QualificationObservation",
                "observation-2",
                criterionId="represented",
                epistemicItemId="item-2",
                observationState="verified",
            ),
        ]
    )
    assert result["next_question"]["result"] == "no_question"
    assert result["readiness"]["result"] == "ready"


def test_contradiction_blocks_readiness_without_model_override() -> None:
    result = _evaluate(
        [
            _canonical(
                "QualificationObservation",
                "observation-1",
                criterionId="timing",
                epistemicItemId="item-1",
                observationState="verified",
            ),
            _canonical(
                "QualificationObservation",
                "observation-2",
                criterionId="represented",
                epistemicItemId="item-2",
                observationState="contradicted",
            ),
        ]
    )
    assert result["next_question"]["result"] == "blocked"
    assert result["readiness"]["result"] == "blocked"
    assert result["readiness"]["blockingCriterionIds"] == ["represented"]


def test_cross_tenant_observation_is_rejected() -> None:
    observation = _canonical(
        "QualificationObservation",
        "observation-1",
        criterionId="timing",
        epistemicItemId="item-1",
        observationState="verified",
    )
    observation["tenantId"] = "other-tenant"
    with pytest.raises(ValueError, match="cross tenant"):
        _evaluate([observation])
