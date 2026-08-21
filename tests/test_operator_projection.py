from __future__ import annotations

from typing import Any

import pytest

from buyer_ops_contracts.operator_projection import (
    JourneyViewDerivationPolicy,
    OperatorProjection,
    _briefing_items,
    journey_view_etag,
)


class _Repository:
    def __init__(self) -> None:
        self.journey = {
            "id": "journey-1",
            "tenantId": "tenant-1",
            "recordType": "BuyerJourney",
            "version": 7,
            "status": "active",
            "effectiveFrom": "2026-01-01T00:00:00Z",
            "buyingPartyId": "party-1",
            "journeyState": "captured",
            "qualificationState": "not_started",
            "representationState": "not_represented",
        }

    def get(self, record_id: str) -> dict[str, Any] | None:
        return self.journey if record_id == self.journey["id"] else None

    def list_by_type(self, record_type: str) -> list[dict[str, Any]]:
        return [self.journey] if record_type == "BuyerJourney" else []

    def current_records(self) -> list[dict[str, Any]]:
        return [self.journey]


def test_projection_fails_closed_without_published_assembler() -> None:
    projection = OperatorProjection(
        _Repository(),  # type: ignore[arg-type]
        tenant_id="tenant-1",
    )

    with pytest.raises(KeyError, match="projection rules are unavailable"):
        projection.journey_view(journey_id="journey-1", principal_id="agent-1")


def test_journey_view_etag_binds_the_closure_identity_and_payload() -> None:
    arguments = {
        "tenant_id": "tenant-1",
        "principal_id": "agent-1",
        "journey_id": "journey-1",
        "canonical_version": 7,
        "compiler_version": "journey_state_v1",
        "view_payload": {"orthogonal_states": {"journey": "active"}},
    }
    first = journey_view_etag(**arguments)
    assert first == journey_view_etag(**arguments)
    assert first != journey_view_etag(**{**arguments, "canonical_version": 8})
    assert first != journey_view_etag(
        **{**arguments, "view_payload": {"orthogonal_states": {"journey": "blocked"}}}
    )


def test_journey_view_etag_rejects_incomplete_identity() -> None:
    with pytest.raises(ValueError, match="identity fields are required"):
        journey_view_etag(
            tenant_id="",
            principal_id="agent-1",
            journey_id="journey-1",
            canonical_version=1,
            compiler_version="journey_state_v1",
            view_payload={},
        )


def test_projection_compiles_published_states_with_explicit_blocker_bindings() -> None:
    projection = OperatorProjection(
        _Repository(),
        tenant_id="tenant-1",
        derivation_policy=JourneyViewDerivationPolicy(
            compiler_version="journey-state/1.0.0",
            blocker_bindings={"contactability_unknown": ("consent", "system")},
        ),
    )
    view = projection.journey_view(
        journey_id="journey-1",
        principal_id="agent-1",
    )
    assert view["orthogonal_states"] == {
        "journey": "captured",
        "contactability": "unknown",
        "acknowledgment": "not_required",
        "qualification": "not_started",
        "consultation": "not_ready",
        "nurture": "inactive",
        "representation": "not_represented",
    }
    assert view["blockers"][0]["code"] == "contactability_unknown"
    assert view["etag"].startswith("sha256:")


def test_briefing_items_are_evidence_linked_and_epistemically_labeled() -> None:
    items = _briefing_items(
        {
            "fact-1": {
                "id": "fact-1",
                "recordType": "VerifiedFact",
                "version": 1,
                "status": "active",
                "digest": "sha256:" + "a" * 64,
                "capturedAt": "2026-08-21T00:00:00Z",
                "claim": "Buyer stated a move timeline.",
            }
        }
    )
    assert items == [
        {
            "item_id": "briefing:fact-1",
            "label": "VerifiedFact",
            "epistemic_state": "verified_fact",
            "summary": "Buyer stated a move timeline.",
            "source_refs": [
                {
                    "record_id": "fact-1",
                    "record_type": "VerifiedFact",
                    "version": 1,
                    "digest": "sha256:" + "a" * 64,
                    "captured_at": "2026-08-21T00:00:00Z",
                }
            ],
            "time_sensitivity": "none",
        }
    ]
