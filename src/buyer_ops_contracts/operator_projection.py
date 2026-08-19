"""Assemble operator-surface/1.1.0 JourneyView from canonical state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .canonical_repository import CanonicalRepository
from .digest import sha256_digest
from .structural import validate_record


def _ref(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": record["id"],
        "record_type": record["recordType"],
        "version": int(record["version"]),
        "status": record["status"],
    }


def _source_ref(record: dict[str, Any]) -> dict[str, Any]:
    captured = record.get("updatedAt") or record.get("createdAt")
    return {
        "record_id": record["id"],
        "record_type": record["recordType"],
        "version": int(record["version"]),
        "digest": sha256_digest(record),
        "captured_at": captured,
    }


def _epistemic_label(record: dict[str, Any]) -> str:
    record_type = record["recordType"]
    mapping = {
        "Evidence": "evidence",
        "Assertion": "assertion",
        "VerifiedFact": "verified_fact",
        "Inference": "inference",
        "Memory": "memory",
        "EpistemicItem": str(record.get("epistemicType", "unknown")),
    }
    return mapping.get(record_type, "unknown")


class OperatorProjection:
    def __init__(self, repository: CanonicalRepository, *, tenant_id: str) -> None:
        self._repository = repository
        self._tenant_id = tenant_id

    def journey_view(self, *, journey_id: str, principal_id: str) -> dict[str, Any]:
        journey = self._repository.get(journey_id)
        if journey is None or journey.get("recordType") != "BuyerJourney":
            raise KeyError("BuyerJourney not found")
        people = self._repository.list_by_type("Person")
        consents = self._repository.list_by_type("ConsentGrant")
        suppressions = self._repository.list_by_type("Suppression")
        observations = [
            item
            for item in self._repository.list_by_type("QualificationObservation")
            if item.get("journeyId") == journey_id
        ]
        appointments = [
            item
            for item in self._repository.list_by_type("Appointment")
            if item.get("journeyId") == journey_id
        ]
        commitments = [
            item
            for item in self._repository.list_by_type("Commitment")
            if item.get("journeyId") == journey_id
        ]
        representations = [
            item
            for item in self._repository.list_by_type("RepresentationRelationship")
            if item.get("buyingPartyId") == journey.get("buyingPartyId")
        ]
        effects = [
            item
            for item in self._repository.list_by_type("EffectAttempt")
            if journey_id in str(item.get("intentId", ""))
            or journey_id in item.get("sourceEvidenceIds", [])
        ]
        evidence_items = self._repository.list_by_type("Evidence")
        assertions = self._repository.list_by_type("Assertion")
        facts = self._repository.list_by_type("VerifiedFact")
        inferences = self._repository.list_by_type("Inference")
        memories = self._repository.list_by_type("Memory")
        workflows = [
            item
            for item in self._repository.list_by_type("WorkflowReference")
            if item.get("subjectId") == journey_id
        ]
        party = self._repository.get(str(journey["buyingPartyId"]))
        member_ids = []
        if party and party.get("members"):
            member_ids = [str(member["personId"]) for member in party["members"]]
        party_people = [person for person in people if person["id"] in member_ids]
        party_consents = [item for item in consents if item.get("personId") in member_ids]
        party_suppressions = [
            item
            for item in suppressions
            if item.get("subjectId") in member_ids and item.get("validityState") == "active"
        ]

        contactability = "unknown"
        if party_suppressions:
            contactability = "suppressed"
        elif party_consents and any(
            item.get("validityState") == "active" for item in party_consents
        ):
            contactability = "contactable"

        acknowledgment = "pending"
        consultation = "not_ready"
        if journey.get("journeyState") == "consultation_ready":
            consultation = "ready"
        if any(item.get("appointmentState") == "proposed" for item in appointments):
            consultation = "offering"
        if any(item.get("appointmentState") == "confirmed" for item in appointments):
            consultation = "booked"

        nurture = "inactive"
        if journey.get("journeyState") == "nurture":
            nurture = "active"
        if journey.get("journeyState") == "dormant":
            nurture = "dormant"

        blockers: list[dict[str, Any]] = []
        if party_people and any(
            person.get("identityState") in {"ambiguous", "conflict"} for person in party_people
        ):
            evidence_refs = [_source_ref(person) for person in party_people]
            # SourceRef record_type must be epistemic; Person is not allowed.
            # Identity blockers cite Evidence records when present, else omit empty.
            evidence_refs = [
                _source_ref(item) for item in evidence_items if item.get("recordType") == "Evidence"
            ][:1]
            if evidence_refs:
                blockers.append(
                    {
                        "code": "identity_unresolved",
                        "category": "identity",
                        "recovery_owner": "agent",
                        "evidence_refs": evidence_refs,
                    }
                )
        if party_suppressions:
            evidence_refs = [
                _source_ref(item) for item in evidence_items if item.get("recordType") == "Evidence"
            ][:1]
            if evidence_refs:
                blockers.append(
                    {
                        "code": "suppressed",
                        "category": "consent",
                        "recovery_owner": "buyer",
                        "evidence_refs": evidence_refs,
                    }
                )
        if not workflows:
            evidence_refs = [
                _source_ref(item) for item in evidence_items if item.get("recordType") == "Evidence"
            ][:1]
            if evidence_refs:
                blockers.append(
                    {
                        "code": "workflow_unbound",
                        "category": "workflow",
                        "recovery_owner": "deployment_operator",
                        "evidence_refs": evidence_refs,
                    }
                )

        briefing: list[dict[str, Any]] = []
        for item in evidence_items + assertions + facts + inferences + memories:
            if item.get("recordType") not in {
                "Evidence",
                "Assertion",
                "VerifiedFact",
                "Inference",
                "Memory",
            }:
                continue
            summary = str(
                item.get("proposition", {}).get("value")
                if isinstance(item.get("proposition"), dict)
                else item.get("summary") or item.get("recordType")
            )
            briefing.append(
                {
                    "item_id": item["id"],
                    "label": item["recordType"],
                    "epistemic_state": _epistemic_label(item),
                    "summary": summary[:500] or item["recordType"],
                    "source_refs": [_source_ref(item)],
                    "time_sensitivity": "none",
                }
            )

        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        view = {
            "message_type": "operator_journey_view",
            "schema_version": "operator-surface/1.1.0",
            "tenant_id": self._tenant_id,
            "principal_id": principal_id,
            "journey_id": journey_id,
            "canonical_version": int(journey["version"]),
            "etag": "",
            "generated_at": now,
            "orthogonal_states": {
                "journey": str(journey.get("journeyState", "captured")),
                "contactability": contactability,
                "acknowledgment": acknowledgment,
                "qualification": str(journey.get("qualificationState", "not_started")),
                "consultation": consultation,
                "nurture": nurture,
                "representation": str(journey.get("representationState", "unconfirmed")),
            },
            "blockers": blockers,
            "next_action_refs": [_ref(item) for item in commitments[:8]],
            "commitment_refs": [_ref(item) for item in commitments],
            "qualification_refs": [_ref(item) for item in observations],
            "consent_refs": [_ref(item) for item in party_consents],
            "representation_refs": [_ref(item) for item in representations],
            "appointment_refs": [_ref(item) for item in appointments],
            "effect_attempt_refs": [_ref(item) for item in effects],
            "briefing_items": briefing[:24],
        }
        view["etag"] = sha256_digest({k: v for k, v in view.items() if k != "etag"})
        validate_record(view, "operator_surface")
        return view

    def list_journey_ids(self) -> list[str]:
        return [item["id"] for item in self._repository.list_by_type("BuyerJourney")]
