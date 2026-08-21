from datetime import UTC, datetime

from buyer_ops_contracts.consultation_runtime import ConsultationRuntime
from buyer_ops_contracts.journey_state import compile_journey_state


def _record(record_type: str, record_id: str, **fields: object) -> dict[str, object]:
    return {
        "id": record_id,
        "tenantId": "tenant-1",
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": record_type,
        "version": 1,
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
        "status": "active",
        "effectiveFrom": "2026-01-01T00:00:00Z",
        "createdBy": {"actorType": "person", "actorId": "agent-1"},
        "sourceEvidenceIds": [f"evidence-{record_id}"],
        **fields,
    }


def _snapshot(journey_state: str = "consultation_ready") -> list[dict[str, object]]:
    return [
        _record(
            "BuyerJourney",
            "journey-1",
            buyingPartyId="party-1",
            ownerLicenseHolderId="agent-1",
            territory="service-area-1",
            journeyState=journey_state,
            qualificationState="sufficient_for_consult",
            representationState="not_represented",
        ),
        _record("BuyingParty", "party-1", members=[{"personId": "person-1", "role": "buyer"}]),
        _record("Person", "person-1", identityState="resolved"),
        _record(
            "ContactEndpoint", "endpoint-1", ownerId="person-1", contactabilityState="contactable"
        ),
        _record(
            "Conversation", "conversation-1", primaryJourneyId="journey-1", linkedJourneyIds=[]
        ),
        _record(
            "Message",
            "message-1",
            conversationId="conversation-1",
            direction="outbound",
            deliveryState="delivered",
        ),
    ]


def test_consultation_runtime_emits_offering_decision_and_premeeting_fields() -> None:
    records = _snapshot()
    journey = records[0]
    state = compile_journey_state(
        tenant_id="tenant-1",
        journey_id="journey-1",
        canonical_version=1,
        records=records,
        observed_at=datetime(2026, 8, 21, tzinfo=UTC),
    ).state
    result = ConsultationRuntime().evaluate(
        journey=journey,
        journey_state=state,
        appointments=[],
        evidence_records=[],
        now=datetime(2026, 8, 21, tzinfo=UTC),
    )
    assert result["consultationState"] == "offering"
    assert result["eligible"] is True
    assert result["preMeetingFields"] == ["buying_goals", "timeline", "financing_readiness"]
    assert "consultation_criteria_met" in result["reasonCodes"]


def test_consultation_runtime_preserves_confirmed_appointment_state() -> None:
    records = _snapshot()
    journey = records[0]
    state = compile_journey_state(
        tenant_id="tenant-1",
        journey_id="journey-1",
        canonical_version=1,
        records=records,
        observed_at=datetime(2026, 8, 21, tzinfo=UTC),
    ).state
    appointment = _record(
        "Appointment",
        "appointment-1",
        journeyId="journey-1",
        appointmentType="consultation",
        participantIds=["person-1", "agent-1"],
        startsAt="2026-08-22T18:00:00Z",
        endsAt="2026-08-22T19:00:00Z",
        timeZone="America/Chicago",
        appointmentState="confirmed",
    )
    result = ConsultationRuntime().evaluate(
        journey=journey,
        journey_state=state,
        appointments=[appointment],
        evidence_records=[],
        now=datetime(2026, 8, 21, tzinfo=UTC),
    )
    assert result["consultationState"] == "booked"
    assert result["appointmentRef"] == {
        "recordId": "appointment-1",
        "recordType": "Appointment",
        "version": 1,
    }
    assert result["reminderPlan"] is None
    assert "reminder_policy_missing" in result["reasonCodes"]
