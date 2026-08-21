from __future__ import annotations

from datetime import UTC, datetime

import pytest

from buyer_ops_contracts.journey_state import (
    JourneyStateCompilationError,
    compile_journey_state,
)

NOW = datetime(2026, 3, 8, 12, tzinfo=UTC)


def _record(record_type: str, record_id: str, **fields: object) -> dict[str, object]:
    return {
        "id": record_id,
        "tenantId": "tenant-1",
        "recordType": record_type,
        "version": 1,
        "status": "active",
        "effectiveFrom": "2026-01-01T00:00:00Z",
        "sourceEvidenceIds": [f"evidence-{record_id}"],
        **fields,
    }


def _records() -> list[dict[str, object]]:
    return [
        _record(
            "BuyerJourney",
            "journey-1",
            buyingPartyId="party-1",
            journeyState="consultation_booked",
            qualificationState="sufficient_for_consult",
            representationState="not_represented",
        ),
        _record(
            "BuyingParty",
            "party-1",
            members=[{"personId": "person-1", "role": "buyer"}],
        ),
        _record("Person", "person-1", identityState="resolved"),
        _record(
            "ContactEndpoint",
            "endpoint-1",
            ownerId="person-1",
            contactabilityState="contactable",
        ),
        _record(
            "Conversation",
            "conversation-1",
            primaryJourneyId="journey-1",
            linkedJourneyIds=[],
        ),
        _record(
            "Message",
            "message-1",
            conversationId="conversation-1",
            direction="outbound",
            deliveryState="unknown_outcome",
        ),
        _record(
            "Appointment",
            "appointment-1",
            journeyId="journey-1",
            appointmentState="unknown_outcome",
        ),
        _record(
            "Commitment",
            "commitment-1",
            journeyId="journey-1",
            commitmentState="open",
            dueAt="2026-03-09T12:00:00Z",
        ),
    ]


def test_compiler_derives_orthogonal_states_and_metadata_deterministically() -> None:
    compilation = compile_journey_state(
        tenant_id="tenant-1",
        journey_id="journey-1",
        canonical_version=7,
        records=reversed(_records()),
        observed_at=NOW,
    )

    assert compilation.state == {
        "message_type": "journey_state",
        "schema_version": "ot01-journey-state/1.0.0",
        "journey_id": "journey-1",
        "canonical_version": 7,
        "ingress_state": "identified",
        "contactability_state": "contactable",
        "acknowledgment_state": "unknown_outcome",
        "qualification_state": "sufficient_for_consult",
        "consultation_state": "provider_pending",
        "nurture_state": "inactive",
        "blocker_codes": [
            "acknowledgment_unknown_outcome",
            "consultation_provider_pending",
        ],
        "next_due_at": "2026-03-09T12:00:00Z",
    }
    assert compilation.input_digest.startswith("sha256:")
    assert compilation.output_digest.startswith("sha256:")
    assert compilation.evidence_ids == tuple(
        sorted(f"evidence-{record['id']}" for record in _records())
    )


def test_compiler_applies_identity_and_suppression_precedence() -> None:
    records = _records()
    records.extend(
        [
            _record("Person", "person-2", identityState="ambiguous"),
            _record(
                "Suppression",
                "suppression-1",
                subjectId="person-1",
                validityState="active",
            ),
        ]
    )
    records[1]["members"] = [
        {"personId": "person-1", "role": "buyer"},
        {"personId": "person-2", "role": "co_buyer"},
    ]

    state = compile_journey_state(
        tenant_id="tenant-1",
        journey_id="journey-1",
        canonical_version=7,
        records=records,
        observed_at=NOW,
    ).state

    assert state["ingress_state"] == "identity_ambiguous"
    assert state["contactability_state"] == "suppressed"
    assert "identity_ambiguous" in state["blocker_codes"]


def test_compiler_applies_rejected_and_blocked_precedence() -> None:
    records = _records()
    journey = records[0]
    journey["journeyState"] = "ineligible"
    records.append(_record("Person", "person-2", identityState="ambiguous"))
    records[1]["members"] = [
        {"personId": "person-1", "role": "buyer"},
        {"personId": "person-2", "role": "co_buyer"},
    ]
    state = compile_journey_state(
        tenant_id="tenant-1",
        journey_id="journey-1",
        canonical_version=7,
        records=records,
        observed_at=NOW,
    ).state
    assert state["ingress_state"] == "rejected"
    assert state["consultation_state"] == "provider_pending"

    journey["journeyState"] = "blocked"
    state = compile_journey_state(
        tenant_id="tenant-1",
        journey_id="journey-1",
        canonical_version=7,
        records=records,
        observed_at=NOW,
    ).state
    assert state["consultation_state"] == "blocked"


def test_compiler_rejects_cross_tenant_and_missing_journey_snapshots() -> None:
    cross_tenant = _records()
    cross_tenant[-1]["tenantId"] = "tenant-other"
    with pytest.raises(JourneyStateCompilationError, match="cross_tenant_snapshot"):
        compile_journey_state(
            tenant_id="tenant-1",
            journey_id="journey-1",
            canonical_version=1,
            records=cross_tenant,
            observed_at=NOW,
        )

    with pytest.raises(JourneyStateCompilationError, match="journey_not_unique"):
        compile_journey_state(
            tenant_id="tenant-1",
            journey_id="missing",
            canonical_version=1,
            records=_records(),
            observed_at=NOW,
        )
