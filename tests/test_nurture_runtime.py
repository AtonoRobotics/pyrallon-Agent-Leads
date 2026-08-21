from datetime import UTC, datetime

from buyer_ops_contracts.nurture_runtime import NurtureRuntime


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
        "createdBy": {"actorType": "service_principal", "actorId": "runtime"},
        "sourceEvidenceIds": ["evidence-1"],
        "status": "active",
        **extra,
    }


def _policy() -> dict:
    return {
        "messageType": "nurture_policy",
        "schemaVersion": "nurture-plan/1.0.0",
        "tenantId": "tenant-1",
        "policyId": "nurture-policy-1",
        "version": 1,
        "lifecycle": "active",
        "effectiveFrom": "2026-01-01T00:00:00Z",
        "effectiveTo": None,
        "channels": ["email", "sms"],
        "approvedActionTypes": ["useful_next_step", "answer_open_question"],
        "stalledAfterSeconds": 3600,
        "maxFrequencySeconds": 86400,
        "sourceEvidenceIds": ["policy-evidence-1"],
    }


def _journey() -> dict:
    return _canonical(
        "BuyerJourney",
        "journey-1",
        buyingPartyId="party-1",
        ownerLicenseHolderId="agent-1",
        territory="Austin",
        journeyState="nurture",
        qualificationState="collecting",
        representationState="not_represented",
    )


def test_nurture_creates_contextual_next_step() -> None:
    result = NurtureRuntime(deriver_id="runtime", implementation_version="1.0.0").plan(
        policy=_policy(),
        journey=_journey(),
        consent_state="granted",
        contactability_state="contactable",
        representation_state="not_represented",
        now=datetime(2026, 1, 1, tzinfo=UTC),
        last_interaction_at="2026-01-01T00:00:00Z",
        unresolved_commitments=[],
    )
    assert result["planState"] == "active"
    assert result["nextAction"]["reasonCodes"] == ["contextual_followup"]


def test_nurture_stops_on_opt_out() -> None:
    result = NurtureRuntime(deriver_id="runtime", implementation_version="1.0.0").plan(
        policy=_policy(),
        journey=_journey(),
        consent_state="opted_out",
        contactability_state="contactable",
        representation_state="not_represented",
        now=datetime(2026, 1, 1, tzinfo=UTC),
        last_interaction_at=None,
        unresolved_commitments=[],
    )
    assert result["planState"] == "paused"
    assert result["nextAction"] is None
    assert "consent_opted_out" in result["stopReasons"]


def test_nurture_prioritizes_overdue_promised_followup() -> None:
    commitment = _canonical(
        "Commitment",
        "commitment-1",
        journeyId="journey-1",
        obligorId="agent-1",
        beneficiaryIds=["party-1"],
        description="send approved briefing",
        dueAt="2025-12-31T00:00:00Z",
        commitmentState="open",
    )
    result = NurtureRuntime(deriver_id="runtime", implementation_version="1.0.0").plan(
        policy=_policy(),
        journey=_journey(),
        consent_state="granted",
        contactability_state="contactable",
        representation_state="not_represented",
        now=datetime(2026, 1, 1, tzinfo=UTC),
        last_interaction_at=None,
        unresolved_commitments=[commitment],
    )
    assert result["nextAction"]["actionType"] == "resolve_promised_followup"
    assert result["unresolvedCommitmentIds"] == ["commitment-1"]
