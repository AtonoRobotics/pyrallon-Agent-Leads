"""Deterministic, consent-bounded reminder planning for confirmed appointments."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .contract_acceptance import canonical_digest
from .structural import validate_record


class ReminderRuntimeError(ValueError):
    """A reminder plan cannot be safely derived from current canonical state."""


class ReminderRuntime:
    """Plan reminders without sending or granting a provider effect."""

    def __init__(self, *, deriver_id: str = "buyer-ops-reminder-runtime") -> None:
        if not deriver_id:
            raise ValueError("reminder deriver identity is required")
        self._deriver_id = deriver_id

    def build_plan(
        self,
        *,
        appointment: dict[str, Any],
        policy: dict[str, Any],
        recipient_endpoints: list[dict[str, Any]],
        consent_state: str,
        contactability_state: str,
        now: datetime,
    ) -> dict[str, Any]:
        validate_record(appointment, "ontology")
        if appointment.get("recordType") != "Appointment":
            raise ReminderRuntimeError("appointment_required")
        if appointment.get("appointmentState") != "confirmed":
            raise ReminderRuntimeError("appointment_not_confirmed")
        if not isinstance(policy, dict) or policy.get("messageType") != "reminder_policy":
            raise ReminderRuntimeError("reminder_policy_missing")
        if policy.get("tenantId") != appointment.get("tenantId"):
            raise ReminderRuntimeError("cross_tenant_reference")
        if policy.get("lifecycle") != "active":
            raise ReminderRuntimeError("reminder_policy_not_active")
        if consent_state != "granted":
            raise ReminderRuntimeError("consent_not_granted")
        if contactability_state != "contactable":
            raise ReminderRuntimeError("contactability_not_confirmed")

        offsets = policy.get("offsetSeconds")
        channels = policy.get("channels")
        if (
            not isinstance(offsets, list)
            or not offsets
            or any(not isinstance(value, int) or value <= 0 for value in offsets)
            or sorted(set(offsets), reverse=True) != offsets
            or not isinstance(channels, list)
            or not channels
            or any(value not in {"email", "sms"} for value in channels)
        ):
            raise ReminderRuntimeError("reminder_policy_invalid")

        candidates = [
            endpoint
            for endpoint in recipient_endpoints
            if endpoint.get("ownerId") in appointment.get("participantIds", [])
            and endpoint.get("endpointState") == "active"
            and endpoint.get("contactabilityState") == "contactable"
            and endpoint.get("channel") in channels
        ]
        if not candidates:
            raise ReminderRuntimeError("recipient_endpoint_missing")
        candidates.sort(key=lambda item: (str(item.get("channel")), str(item.get("id"))))
        starts_at = _timestamp(appointment.get("startsAt"))
        now = now.astimezone(UTC)
        reminders: list[dict[str, Any]] = []
        for offset in offsets:
            due_at = starts_at - timedelta(seconds=offset)
            if due_at <= now:
                continue
            endpoint = candidates[0]
            reminders.append(
                {
                    "reminderId": f"reminder:{appointment['id']}:{offset}",
                    "appointmentRef": _ref(appointment),
                    "recipientRef": _ref(endpoint),
                    "channel": str(endpoint["channel"]),
                    "offsetSeconds": offset,
                    "dueAt": due_at.isoformat().replace("+00:00", "Z"),
                    "state": "scheduled",
                }
            )
        evidence_ids = sorted(
            {
                str(value)
                for value in appointment.get("sourceEvidenceIds", [])
                if isinstance(value, str)
            }
            | {
                str(value)
                for value in policy.get("sourceEvidenceIds", [])
                if isinstance(value, str)
            }
        )
        if not evidence_ids:
            raise ReminderRuntimeError("reminder_evidence_missing")
        payload = {
            "appointment": appointment,
            "policy": policy,
            "recipientEndpoints": candidates,
            "consentState": consent_state,
            "contactabilityState": contactability_state,
            "reminders": reminders,
        }
        return {
            "messageType": "reminder_plan",
            "schemaVersion": "consultation-operation/1.0.0",
            "tenantId": appointment["tenantId"],
            "planId": f"reminder-plan:{appointment['id']}:{appointment['version']}",
            "appointmentRef": _ref(appointment),
            "policyRef": _ref(policy),
            "state": "scheduled" if reminders else "complete",
            "reminders": reminders,
            "derivedBy": {
                "principalId": self._deriver_id,
                "implementationId": "reminder_plan_v1",
                "implementationVersion": "1.0.0",
            },
            "derivedAt": now.isoformat().replace("+00:00", "Z"),
            "inputDigest": canonical_digest(payload),
            "evidenceIds": evidence_ids,
        }


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ReminderRuntimeError("appointment_time_missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ReminderRuntimeError("appointment_time_requires_offset")
    return parsed.astimezone(UTC)


def _ref(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "recordId": str(record["id"]),
        "recordType": str(record["recordType"]),
        "version": int(record["version"]),
    }
