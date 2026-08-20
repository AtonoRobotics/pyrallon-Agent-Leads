"""Executable semantic acceptance rules for the 1.0 qualification and booking families."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ContractSemanticError(ValueError):
    """A schema-valid record violates a cross-field governing rule."""


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ContractSemanticError("timestamp_requires_offset")
    return parsed


def canonical_digest(value: Any) -> str:
    """Return the contract's stable SHA-256 digest over sorted compact UTF-8 JSON."""

    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


def validate_policy_version(current: dict[str, Any], predecessor: dict[str, Any] | None) -> None:
    version = current["version"]
    supersedes = current.get("supersedesRecordId")
    if version == 1:
        if predecessor is not None or supersedes is not None:
            raise ContractSemanticError("version_one_has_no_predecessor")
        return
    if predecessor is None:
        raise ContractSemanticError("predecessor_required")
    if predecessor["tenantId"] != current["tenantId"]:
        raise ContractSemanticError("cross_tenant_predecessor")
    if predecessor["policyId"] != current["policyId"]:
        raise ContractSemanticError("stable_policy_identity_required")
    if predecessor["version"] + 1 != version:
        raise ContractSemanticError("non_contiguous_policy_version")
    if supersedes != predecessor["policyId"]:
        raise ContractSemanticError("supersedes_identity_mismatch")


def validate_qualification(policy: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    if policy["tenantId"] != inputs["tenantId"]:
        raise ContractSemanticError("cross_tenant_reference")
    if policy["lifecycle"] != "active":
        raise ContractSemanticError("policy_not_active")
    criteria = {item["criterionId"]: item for item in policy["criteria"]}
    if len(criteria) != len(policy["criteria"]):
        raise ContractSemanticError("duplicate_criterion")
    observations: dict[str, dict[str, Any]] = {}
    evaluated_at = _time(inputs["evaluatedAt"])
    for observation in inputs["observations"]:
        criterion_id = observation["criterionId"]
        if criterion_id not in criteria:
            raise ContractSemanticError("unknown_criterion")
        if criterion_id in observations:
            raise ContractSemanticError("duplicate_observation")
        age = (evaluated_at - _time(observation["observedAt"])).total_seconds()
        state = observation["observationState"]
        expected_valid = (
            0 <= age <= criteria[criterion_id]["maxAgeSeconds"]
            and state in criteria[criterion_id]["acceptedObservationStates"]
            and not observation["contradictionRefs"]
            and state != "inferred"
        )
        if observation["validAtEvaluation"] != expected_valid:
            raise ContractSemanticError("invalid_freshness_or_state_marker")
        observations[criterion_id] = observation
    expected_digest = canonical_digest(
        {key: inputs[key] for key in sorted(inputs) if key not in {"inputDigest", "inputSetId"}}
    )
    if inputs["inputDigest"] != expected_digest:
        raise ContractSemanticError("input_digest_mismatch")
    return observations


def select_next_question(policy: dict[str, Any], inputs: dict[str, Any]) -> tuple[str, str | None]:
    observations = validate_qualification(policy, inputs)
    unresolved: list[tuple[int, str, str]] = []
    for criterion in policy["criteria"]:
        observation = observations.get(criterion["criterionId"])
        if observation is not None and observation["validAtEvaluation"]:
            continue
        disposition = (
            criterion["contradictionDisposition"]
            if observation is not None and observation["contradictionRefs"]
            else criterion["missingDisposition"]
        )
        result = "ask" if disposition in {"ask", "ask_clarification"} else "agent_handle"
        if disposition in {"block_readiness", "ignore"}:
            continue
        unresolved.append((criterion["priority"], criterion["criterionId"], result))
    if not unresolved:
        return ("no_question", None)
    _, criterion_id, result = min(unresolved)
    return (result, criterion_id)


def readiness_result(policy: dict[str, Any], inputs: dict[str, Any]) -> tuple[str, list[str]]:
    observations = validate_qualification(policy, inputs)
    blocking: list[str] = []
    policy_blocking: list[str] = []
    for criterion in policy["criteria"]:
        observation = observations.get(criterion["criterionId"])
        if (
            observation is not None
            and observation["contradictionRefs"]
            and criterion["contradictionDisposition"] == "block_readiness"
        ):
            policy_blocking.append(criterion["criterionId"])
        if criterion["disposition"] not in {"required", "declinable"}:
            continue
        if observation is None or not observation["validAtEvaluation"]:
            blocking.append(criterion["criterionId"])
    if inputs["urgentEscalationRefs"] or policy_blocking:
        return ("blocked", sorted(set(blocking + policy_blocking)))
    if not inputs["serviceZoneEligible"] or not inputs["capacityAvailable"] or blocking:
        return ("not_ready", sorted(blocking))
    return ("ready", [])


def validate_availability_policy(policy: dict[str, Any]) -> None:
    if policy["lifecycle"] != "active":
        raise ContractSemanticError("policy_not_active")
    try:
        ZoneInfo(policy["timeZone"])
    except ZoneInfoNotFoundError as error:
        raise ContractSemanticError("unknown_time_zone") from error
    for window in policy["weeklyWindows"]:
        if window["localStart"] >= window["localEnd"]:
            raise ContractSemanticError("non_positive_weekly_window")
    for interval in policy["blackouts"]:
        if _time(interval["startsAt"]) >= _time(interval["endsAt"]):
            raise ContractSemanticError("non_positive_blackout")


def validate_calendar_snapshot(snapshot: dict[str, Any]) -> None:
    if _time(snapshot["rangeStart"]) >= _time(snapshot["rangeEnd"]):
        raise ContractSemanticError("non_positive_snapshot_range")
    for interval in snapshot["busyIntervals"]:
        if _time(interval["startsAt"]) >= _time(interval["endsAt"]):
            raise ContractSemanticError("non_positive_busy_interval")


def local_window_instants(
    policy: dict[str, Any], local_date: date
) -> list[tuple[datetime, datetime]]:
    """Resolve policy windows to UTC, choosing fold=0 and rejecting nonexistent times."""

    validate_availability_policy(policy)
    zone = ZoneInfo(policy["timeZone"])
    result: list[tuple[datetime, datetime]] = []
    for window in policy["weeklyWindows"]:
        if window["dayOfWeek"] != local_date.isoweekday():
            continue
        start_clock = time.fromisoformat(window["localStart"])
        end_clock = time.fromisoformat(window["localEnd"])
        start = datetime.combine(local_date, start_clock, zone).replace(fold=0)
        end = datetime.combine(local_date, end_clock, zone).replace(fold=0)
        if start.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != start.replace(
            tzinfo=None
        ):
            continue
        if end.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != end.replace(tzinfo=None):
            continue
        result.append((start.astimezone(UTC), end.astimezone(UTC)))
    return result


def validate_slot_set(slot_set: dict[str, Any], policy: dict[str, Any]) -> None:
    validate_availability_policy(policy)
    derived = _time(slot_set["derivedAt"])
    expires = _time(slot_set["expiresAt"])
    if (
        not derived
        < expires
        <= derived.fromtimestamp(
            derived.timestamp() + min(policy["slotSetTtlSeconds"], 900),
            tz=derived.tzinfo,
        )
    ):
        raise ContractSemanticError("invalid_slot_set_expiry")
    ordering = [
        (slot["startsAt"], slot["locationId"], slot["slotId"]) for slot in slot_set["slots"]
    ]
    if ordering != sorted(ordering):
        raise ContractSemanticError("slot_ordering_mismatch")
    for slot in slot_set["slots"]:
        if _time(slot["startsAt"]) >= _time(slot["endsAt"]):
            raise ContractSemanticError("non_positive_slot")
        payload = {key: slot[key] for key in sorted(slot) if key not in {"slotId", "slotDigest"}}
        expected = canonical_digest(payload)
        if slot["slotDigest"] != expected or slot["slotId"] != expected.split(":", 1)[1]:
            raise ContractSemanticError("slot_identity_mismatch")


def validate_booking_command(command: dict[str, Any]) -> None:
    kind = command["commandKind"]
    slot_values = [
        command["slotSetRef"],
        command["selectedSlotId"],
        command["selectedSlotDigest"],
    ]
    if kind in {"book", "reschedule"} and any(value is None for value in slot_values):
        raise ContractSemanticError("slot_required")
    if kind == "book" and (
        command["appointmentRef"] is not None or command["expectedAppointmentVersion"] is not None
    ):
        raise ContractSemanticError("book_requires_absent_appointment")
    if kind == "cancel" and any(value is not None for value in slot_values):
        raise ContractSemanticError("cancel_forbids_slot")
    if kind in {"reschedule", "cancel"} and (
        command["appointmentRef"] is None or command["expectedAppointmentVersion"] is None
    ):
        raise ContractSemanticError("existing_appointment_required")


def validate_booking_context(
    command: dict[str, Any],
    *,
    binding: dict[str, Any],
    current_provider_watermark: str,
    current_appointment_version: int | None,
    authority_active: bool,
) -> None:
    """Fail closed across authority, binding, watermark, expiry, and optimistic concurrency."""

    validate_booking_command(command)
    if command["tenantId"] != binding["tenantId"]:
        raise ContractSemanticError("cross_tenant_reference")
    if binding["lifecycle"] != "active":
        raise ContractSemanticError("provider_binding_not_active")
    if not authority_active:
        raise ContractSemanticError("authority_not_active")
    if command["providerWatermark"] != current_provider_watermark:
        raise ContractSemanticError("stale_provider_watermark")
    if _time(command["expiresAt"]) <= datetime.now(UTC):
        raise ContractSemanticError("command_expired")
    if command["expectedAppointmentVersion"] != current_appointment_version:
        raise ContractSemanticError("appointment_version_conflict")


def admit_idempotency(command: dict[str, Any], existing_payload_digest: str | None) -> str:
    """Return new/duplicate or reject reuse of a key for a different payload."""

    if existing_payload_digest is None:
        return "new"
    if existing_payload_digest == command["payloadDigest"]:
        return "duplicate"
    raise ContractSemanticError("idempotency_key_payload_conflict")


def validate_reconciliation(prior_result: dict[str, Any], reconciliation: dict[str, Any]) -> None:
    if prior_result["tenantId"] != reconciliation["tenantId"]:
        raise ContractSemanticError("cross_tenant_reference")
    if prior_result["state"] != "unknown_outcome":
        raise ContractSemanticError("reconciliation_requires_unknown_outcome")
    terminal = reconciliation["result"] in {"confirmed", "cancelled"}
    if terminal != (
        reconciliation["appointmentRef"] is not None
        and reconciliation["appointmentVersion"] is not None
    ):
        raise ContractSemanticError("appointment_evidence_mismatch")
