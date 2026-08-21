"""Policy-bounded consultation conversion and evidence-linked briefing runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .contract_acceptance import canonical_digest
from .reminder_runtime import ReminderRuntime, ReminderRuntimeError
from .structural import validate_record


class ConsultationRuntimeError(ValueError):
    pass


class ConsultationRuntime:
    """Derive consultation readiness without inventing buyer facts."""

    def __init__(self, *, deriver_id: str = "buyer-ops-consultation-runtime") -> None:
        if not deriver_id:
            raise ValueError("consultation deriver identity is required")
        self._deriver_id = deriver_id

    def evaluate(
        self,
        *,
        journey: dict[str, Any],
        journey_state: dict[str, Any],
        appointments: list[dict[str, Any]],
        evidence_records: list[dict[str, Any]],
        now: datetime,
        reminder_policy: dict[str, Any] | None = None,
        recipient_endpoints: list[dict[str, Any]] | None = None,
        consent_state: str = "unknown",
        contactability_state: str = "unknown",
    ) -> dict[str, Any]:
        validate_record(journey, "ontology")
        validate_record(journey_state, "temporal")
        if journey_state.get("journey_id") != journey.get("id"):
            raise ConsultationRuntimeError("journey_state_scope_mismatch")
        now = now.astimezone(UTC)
        current_appointments = [
            appointment
            for appointment in appointments
            if appointment.get("recordType") == "Appointment"
            and appointment.get("journeyId") == journey["id"]
            and appointment.get("status") in {"active", "current"}
        ]
        current_appointments.sort(
            key=lambda item: str(item.get("updatedAt", item.get("startsAt", "")))
        )
        appointment = current_appointments[-1] if current_appointments else None
        appointment_state = str(appointment.get("appointmentState")) if appointment else ""

        reasons: list[str] = []
        journey_state_name = str(journey_state.get("consultation_state", "not_ready"))
        qualification_state = str(journey_state.get("qualification_state", "not_started"))
        eligible = qualification_state in {"ready", "consultation_ready"} or journey_state_name in {
            "ready",
            "offering",
            "provider_pending",
            "booked",
            "completed",
            "cancelled",
        }
        if not eligible:
            reasons.append("qualification_not_ready")

        state = "ready" if eligible else "not_ready"
        if appointment_state in {"provider_pending", "unknown_outcome"}:
            state = "provider_pending"
            reasons = ["appointment_provider_pending"]
        elif appointment_state in {"confirmed", "completed", "cancelled", "no_show"}:
            state = {
                "confirmed": "booked",
                "completed": "completed",
                "cancelled": "cancelled",
                "no_show": "blocked",
            }[appointment_state]
            reasons = [f"appointment_{appointment_state}"]
        elif eligible:
            state = "offering"
            reasons.append("consultation_criteria_met")

        pre_meeting_fields = ["buying_goals", "timeline", "financing_readiness"]
        if state in {"booked", "completed"}:
            reasons.append("pre_meeting_information_requested")
        briefing_items = _briefing_items(evidence_records)
        reminder_plan = None
        if appointment is not None and appointment_state == "confirmed":
            if reminder_policy is None:
                reasons.append("reminder_policy_missing")
            else:
                try:
                    reminder_plan = ReminderRuntime().build_plan(
                        appointment=appointment,
                        policy=reminder_policy,
                        recipient_endpoints=recipient_endpoints or [],
                        consent_state=consent_state,
                        contactability_state=contactability_state,
                        now=now,
                    )
                except ReminderRuntimeError as exc:
                    reasons.append(f"reminder_{exc}")
        evidence_ids = sorted(
            {str(record["id"]) for record in evidence_records if isinstance(record.get("id"), str)}
            | {str(journey["id"])}
        )
        payload = {
            "tenantId": journey["tenantId"],
            "journeyId": journey["id"],
            "canonicalVersion": int(journey["version"]),
            "journeyState": journey_state,
            "appointment": appointment,
            "evidenceIds": evidence_ids,
        }
        result: dict[str, Any] = {
            "messageType": "consultation_decision",
            "schemaVersion": "consultation-operation/1.0.0",
            "tenantId": journey["tenantId"],
            "journeyId": journey["id"],
            "canonicalVersion": int(journey["version"]),
            "decisionId": f"consultation:{journey['id']}:{journey['version']}",
            "consultationState": state,
            "eligible": eligible,
            "reasonCodes": sorted(set(reasons)),
            "appointmentRef": _record_ref(appointment) if appointment else None,
            "preMeetingFields": pre_meeting_fields,
            "briefingItems": briefing_items,
            "reminderPlan": reminder_plan,
            "derivedAt": now.isoformat().replace("+00:00", "Z"),
            "inputDigest": canonical_digest(payload),
            "evidenceIds": evidence_ids,
        }
        validate_record(result, "consultation_operation")
        return result


def _record_ref(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "recordId": str(record["id"]),
        "recordType": str(record["recordType"]),
        "version": int(record["version"]),
    }


def _briefing_items(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    states = {
        "Evidence": "evidence",
        "Assertion": "assertion",
        "VerifiedFact": "verified_fact",
        "Inference": "inference",
        "Memory": "memory",
        "DocumentArtifact": "evidence",
    }
    items: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: str(item.get("id", ""))):
        record_id = record.get("id")
        record_type = str(record.get("recordType", ""))
        if not isinstance(record_id, str) or record_type not in states:
            continue
        summary = next(
            (
                str(record[key]).strip()
                for key in ("summary", "claim", "statement", "content", "title")
                if isinstance(record.get(key), str) and str(record[key]).strip()
            ),
            f"{record_type} {record_id}",
        )
        items.append(
            {
                "itemId": f"consultation-briefing:{record_id}",
                "label": record_type,
                "epistemicState": states[record_type],
                "summary": summary,
                "evidenceIds": [record_id],
                "timeSensitivity": "none",
            }
        )
    return items
