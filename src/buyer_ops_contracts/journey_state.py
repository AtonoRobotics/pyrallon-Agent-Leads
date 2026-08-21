"""Deterministic canonical-to-Temporal JourneyState compilation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .contract_acceptance import canonical_digest
from .structural import validate_record


class JourneyStateCompilationError(ValueError):
    """Raised when a current canonical snapshot cannot produce a governed state."""


@dataclass(frozen=True, slots=True)
class JourneyStateCompilation:
    state: dict[str, Any]
    input_digest: str
    output_digest: str
    evidence_ids: tuple[str, ...]


_CONTACTABILITY_ORDER = {
    "unknown": 0,
    "contactable": 1,
    "temporarily_unavailable": 2,
    "invalid": 3,
    "suppressed": 4,
}
_ACKNOWLEDGMENT_ORDER = {
    "not_required": 0,
    "sent": 1,
    "delivered": 2,
    "pending": 3,
    "failed": 4,
    "unknown_outcome": 5,
}
_APPOINTMENT_ORDER = {
    "cancelled": 0,
    "no_show": 1,
    "confirmed": 2,
    "completed": 3,
    "provider_pending": 4,
    "unknown_outcome": 5,
}


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise JourneyStateCompilationError("timestamp_requires_offset")
    return parsed.astimezone(UTC)


def _current(record: dict[str, Any], observed_at: datetime) -> bool:
    if record.get("status") not in {"active", "current"}:
        return False
    effective_from = record.get("effectiveFrom")
    effective_to = record.get("effectiveTo")
    if not isinstance(effective_from, str) or _timestamp(effective_from) > observed_at:
        return False
    return effective_to is None or _timestamp(effective_to) > observed_at


def _record_digest(record: dict[str, Any]) -> str:
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


def _related_records(
    records: list[dict[str, Any]], journey: dict[str, Any], journey_id: str
) -> list[dict[str, Any]]:
    buying_party_id = journey.get("buyingPartyId")
    buying_party = next(
        (
            record
            for record in records
            if record.get("recordType") == "BuyingParty" and record.get("id") == buying_party_id
        ),
        None,
    )
    person_ids = {
        member.get("personId")
        for member in (buying_party or {}).get("members", [])
        if isinstance(member, dict) and isinstance(member.get("personId"), str)
    }
    conversation_ids = {
        record["id"]
        for record in records
        if record.get("recordType") == "Conversation"
        and (
            record.get("primaryJourneyId") == journey_id
            or journey_id in record.get("linkedJourneyIds", [])
        )
    }
    related: list[dict[str, Any]] = []
    for record in records:
        record_type = record.get("recordType")
        journey_ref = record.get("journeyRef")
        is_related = (
            record.get("id") == journey_id
            or record.get("journeyId") == journey_id
            or (isinstance(journey_ref, dict) and journey_ref.get("recordId") == journey_id)
            or (record_type == "BuyingParty" and record.get("id") == buying_party_id)
            or (record_type == "Person" and record.get("id") in person_ids)
            or (record_type == "ContactEndpoint" and record.get("ownerId") in person_ids)
            or (
                record_type in {"Suppression", "ConsentGrant"}
                and (
                    record.get("subjectId") in person_ids | {journey_id}
                    or record.get("personId") in person_ids
                )
            )
            or (
                record_type == "RepresentationRelationship"
                and record.get("buyingPartyId") == buying_party_id
            )
            or (record_type == "WorkflowReference" and record.get("subjectId") == journey_id)
            or (record_type == "Message" and record.get("conversationId") in conversation_ids)
            or (record_type == "Conversation" and record.get("id") in conversation_ids)
        )
        if is_related:
            related.append(record)
    return sorted(
        related, key=lambda record: (str(record.get("recordType")), str(record.get("id")))
    )


def _choose(
    values: Iterable[tuple[str, str]],
    order: dict[str, int],
    *,
    default: str,
    conflict_code: str,
    blockers: list[str],
) -> str:
    candidates = list(values)
    if not candidates:
        return default
    highest = max(order.get(value, -1) for value, _ in candidates)
    selected = sorted({value for value, _ in candidates if order.get(value, -1) == highest})
    if len(selected) > 1:
        blockers.append(conflict_code)
        return default
    return selected[0]


def compile_journey_state(
    *,
    tenant_id: str,
    journey_id: str,
    canonical_version: int,
    records: Iterable[dict[str, Any]],
    observed_at: datetime,
) -> JourneyStateCompilation:
    """Compile one current tenant/journey snapshot into the Temporal state contract.

    The compiler consumes only current records. It never chooses among equal-precedence
    states by database order; ambiguity becomes a blocker and the schema's conservative
    state. Deployment policy values are not invented here.
    """

    if not tenant_id or not journey_id or canonical_version < 1:
        raise JourneyStateCompilationError("scope_required")
    observed_at = observed_at.astimezone(UTC)
    snapshot = list(records)
    if any(record.get("tenantId") != tenant_id for record in snapshot):
        raise JourneyStateCompilationError("cross_tenant_snapshot")
    current = [record for record in snapshot if _current(record, observed_at)]
    journeys = [
        record
        for record in current
        if record.get("recordType") == "BuyerJourney" and record.get("id") == journey_id
    ]
    if len(journeys) != 1:
        raise JourneyStateCompilationError("journey_not_unique")
    journey = journeys[0]
    related = _related_records(current, journey, journey_id)
    blockers: list[str] = []

    people = [record for record in related if record.get("recordType") == "Person"]
    identity_states = {record.get("identityState") for record in people}
    if journey.get("journeyState") in {"ineligible", "released"}:
        ingress_state = "rejected"
    elif identity_states & {"ambiguous", "conflict"}:
        ingress_state = "identity_ambiguous"
        blockers.append("identity_ambiguous")
    elif journey.get("journeyState") == "captured":
        ingress_state = "captured"
    else:
        ingress_state = "identified"

    suppressions = [
        record
        for record in related
        if record.get("recordType") == "Suppression" and record.get("validityState") == "active"
    ]
    endpoints = [record for record in related if record.get("recordType") == "ContactEndpoint"]
    contactability = _choose(
        (("suppressed", str(record["id"])) for record in suppressions),
        _CONTACTABILITY_ORDER,
        default="unknown",
        conflict_code="contactability_conflict",
        blockers=blockers,
    )
    if contactability == "unknown":
        contactability = _choose(
            (
                (str(record.get("contactabilityState")), str(record["id"]))
                for record in endpoints
                if record.get("contactabilityState") in _CONTACTABILITY_ORDER
            ),
            _CONTACTABILITY_ORDER,
            default="unknown",
            conflict_code="contactability_conflict",
            blockers=blockers,
        )
        if contactability == "unknown":
            blockers.append("contactability_unknown")

    conversations = {
        record["id"] for record in related if record.get("recordType") == "Conversation"
    }
    messages = [
        record
        for record in related
        if record.get("recordType") == "Message"
        and record.get("conversationId") in conversations
        and record.get("direction") == "outbound"
    ]
    acknowledgment = _choose(
        (
            (
                {"queued": "pending", "observed": "pending"}.get(
                    str(record.get("deliveryState")), str(record.get("deliveryState"))
                ),
                str(record["id"]),
            )
            for record in messages
            if record.get("deliveryState")
            in {"queued", "observed", "sent", "delivered", "failed", "unknown_outcome"}
        ),
        _ACKNOWLEDGMENT_ORDER,
        default="not_required",
        conflict_code="acknowledgment_conflict",
        blockers=blockers,
    )
    if acknowledgment == "unknown_outcome":
        blockers.append("acknowledgment_unknown_outcome")

    qualification_state = journey.get("qualificationState")
    if qualification_state not in {
        "not_started",
        "collecting",
        "sufficient_for_consult",
        "stale",
        "contradicted",
        "declined",
    }:
        raise JourneyStateCompilationError("qualification_state_missing")
    if qualification_state in {"stale", "contradicted", "declined"}:
        blockers.append(f"qualification_{qualification_state}")

    journey_state = str(journey.get("journeyState"))
    appointments = [record for record in related if record.get("recordType") == "Appointment"]
    consultation_candidates: list[tuple[str, str]] = []
    for record in appointments:
        appointment_state = str(record.get("appointmentState"))
        mapped = {"confirmed": "booked", "unknown_outcome": "provider_pending"}.get(
            appointment_state, appointment_state
        )
        if mapped in {"provider_pending", "booked", "completed", "cancelled", "no_show"}:
            consultation_candidates.append((mapped, str(record["id"])))
    consultation_order = {
        "cancelled": 0,
        "no_show": 1,
        "booked": 2,
        "completed": 3,
        "provider_pending": 4,
    }
    if journey_state == "blocked":
        consultation_state = "blocked"
    elif consultation_candidates:
        consultation_state = _choose(
            consultation_candidates,
            consultation_order,
            default="blocked",
            conflict_code="consultation_conflict",
            blockers=blockers,
        )
    else:
        consultation_state = {
            "consultation_ready": "ready",
            "consultation_booked": "booked",
            "blocked": "blocked",
        }.get(str(journey.get("journeyState")), "not_ready")
    if consultation_state == "provider_pending":
        blockers.append("consultation_provider_pending")

    nurture_state = {
        "nurture": "active",
        "dormant": "dormant",
        "closed": "completed",
    }.get(journey_state, "inactive")
    relationships = [
        record for record in related if record.get("recordType") == "RepresentationRelationship"
    ]
    if any(record.get("relationshipState") == "conflict" for record in relationships):
        blockers.append("representation_conflict")

    due_values = [
        _timestamp(record["dueAt"])
        for record in related
        if record.get("recordType") == "Commitment"
        and record.get("commitmentState") in {"open", "in_progress", "blocked"}
        and isinstance(record.get("dueAt"), str)
    ]
    next_due_at = min(due_values) if due_values else None
    state: dict[str, Any] = {
        "message_type": "journey_state",
        "schema_version": "ot01-journey-state/1.0.0",
        "journey_id": journey_id,
        "canonical_version": canonical_version,
        "ingress_state": ingress_state,
        "contactability_state": contactability,
        "acknowledgment_state": acknowledgment,
        "qualification_state": qualification_state,
        "consultation_state": consultation_state,
        "nurture_state": nurture_state,
        "blocker_codes": sorted(set(blockers)),
    }
    if next_due_at is not None:
        state["next_due_at"] = next_due_at.isoformat().replace("+00:00", "Z")
    validate_record(state, "temporal")
    input_digest = canonical_digest(
        {
            "tenant_id": tenant_id,
            "journey_id": journey_id,
            "canonical_version": canonical_version,
            "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
            "records": related,
        }
    )
    evidence_ids = tuple(
        sorted({str(item) for record in related for item in record.get("sourceEvidenceIds", [])})
    )
    return JourneyStateCompilation(
        state=state,
        input_digest=input_digest,
        output_digest=canonical_digest(state),
        evidence_ids=evidence_ids,
    )
