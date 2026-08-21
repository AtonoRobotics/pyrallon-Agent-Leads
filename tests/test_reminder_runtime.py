from datetime import UTC, datetime

import pytest

from buyer_ops_contracts.reminder_runtime import ReminderRuntime, ReminderRuntimeError


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


def test_reminder_runtime_plans_only_future_consent_bounded_reminders() -> None:
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
    policy = _record(
        "ReminderPolicy",
        "reminder-policy-1",
        messageType="reminder_policy",
        lifecycle="active",
        offsetSeconds=[86400, 3600],
        channels=["email"],
    )
    endpoint = _record(
        "ContactEndpoint",
        "endpoint-1",
        ownerId="person-1",
        channel="email",
        endpointState="active",
        contactabilityState="contactable",
    )
    result = ReminderRuntime().build_plan(
        appointment=appointment,
        policy=policy,
        recipient_endpoints=[endpoint],
        consent_state="granted",
        contactability_state="contactable",
        now=datetime(2026, 8, 21, 12, tzinfo=UTC),
    )
    assert result["state"] == "scheduled"
    assert [item["offsetSeconds"] for item in result["reminders"]] == [86400, 3600]
    assert result["reminders"][0]["recipientRef"]["recordId"] == "endpoint-1"


def test_reminder_runtime_fails_closed_without_consent_or_recipient() -> None:
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
    policy = _record(
        "ReminderPolicy",
        "reminder-policy-1",
        messageType="reminder_policy",
        lifecycle="active",
        offsetSeconds=[3600],
        channels=["sms"],
    )
    with pytest.raises(ReminderRuntimeError, match="consent_not_granted"):
        ReminderRuntime().build_plan(
            appointment=appointment,
            policy=policy,
            recipient_endpoints=[],
            consent_state="unknown",
            contactability_state="unknown",
            now=datetime(2026, 8, 21, 12, tzinfo=UTC),
        )
