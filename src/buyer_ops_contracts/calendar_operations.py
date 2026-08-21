"""Durable calendar operations composed from contracts and governed effects.

This module is deliberately the shared boundary for HTTP and Temporal callers:
availability is derived from published inputs and appended, while booking and
reconciliation append the provider outcome only after the calendar contract has
validated the command context.  Provider credentials and transport remain in
the connector runtime.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from .calendar_runtime import CalendarProvider, CalendarRuntime
from .contract_acceptance import canonical_digest
from .derived_contract_repository import BookingOutcomeRepository, SlotSetRepository


class CalendarEffectInvoker(Protocol):
    def __call__(
        self,
        request: dict[str, Any],
        payload: bytes,
        *,
        permit_digest: str,
        preview: dict[str, Any] | None,
    ) -> dict[str, Any]: ...

    def reconcile(
        self,
        request: dict[str, Any],
        provider_receipt_id: str,
        *,
        permit_digest: str,
    ) -> dict[str, Any]: ...


class ConnectorCalendarProvider:
    """Adapt one already-admitted connector effect to ``CalendarProvider``."""

    def __init__(
        self,
        invoker: CalendarEffectInvoker,
        *,
        request_for: Callable[[dict[str, Any], str, bytes], dict[str, Any]],
        permit_digest: str,
    ) -> None:
        if not permit_digest:
            raise ValueError("calendar provider permit_digest is required")
        self._invoker = invoker
        self._request_for = request_for
        self._permit_digest = permit_digest

    def book(self, command: dict[str, Any], slot: dict[str, Any]) -> dict[str, Any]:
        return self._execute(command, "calendar.book", _calendar_payload(command, slot))

    def snapshot(
        self, binding: dict[str, Any], *, range_start: str, range_end: str
    ) -> dict[str, Any]:
        payload = {
            "action": "calendar.availability",
            "calendarId": binding["calendarId"],
            "timeMin": range_start,
            "timeMax": range_end,
        }
        raw_payload = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        request = self._request_for(binding, "calendar.availability", raw_payload)
        response = self._invoker(
            request,
            raw_payload,
            permit_digest=self._permit_digest,
            preview=None,
        )
        if response.get("outcome") != "confirmed":
            raise ValueError("calendar availability provider did not confirm the read")
        receipt = str(response.get("receiptId") or request.get("requestId") or "")
        if not receipt:
            raise ValueError("calendar availability provider response has no receipt")
        provider_response = response.get("providerResponse")
        busy_intervals = _busy_intervals(provider_response)
        watermark = (
            str(
                (provider_response or {}).get("nextSyncToken")
                or (provider_response or {}).get("etag")
                or receipt
            )
            if isinstance(provider_response, dict)
            else receipt
        )
        snapshot_material = {
            "binding": binding["bindingId"],
            "rangeStart": range_start,
            "rangeEnd": range_end,
            "providerWatermark": watermark,
            "busyIntervals": busy_intervals,
        }
        return {
            "messageType": "calendar_snapshot",
            "schemaVersion": "availability-booking/1.0.0",
            "tenantId": binding["tenantId"],
            "snapshotId": _stable_id("calendar-snapshot", receipt, range_start, range_end),
            "providerBindingRef": {
                "recordId": binding["bindingId"],
                "recordType": "CalendarProviderBinding",
                "version": binding["version"],
            },
            "observedAt": _now(),
            "rangeStart": range_start,
            "rangeEnd": range_end,
            "providerWatermark": watermark,
            "providerVersion": str(response.get("providerVersion") or "unknown"),
            "busyIntervals": busy_intervals,
            "snapshotDigest": canonical_digest(snapshot_material),
            "sourceEvidenceIds": [receipt],
        }

    def reschedule(self, command: dict[str, Any], slot: dict[str, Any]) -> dict[str, Any]:
        return self._execute(command, "calendar.reschedule", _calendar_payload(command, slot))

    def cancel(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._execute(
            command,
            "calendar.cancel",
            {
                "action": "calendar.cancel",
                "calendarId": command["providerBindingRef"]["recordId"],
                "id": command["appointmentRef"]["recordId"],
            },
        )

    def reconcile(self, prior_result: dict[str, Any]) -> dict[str, Any]:
        request = self._request_for(prior_result, "calendar.reconcile", b"")
        response = self._invoker.reconcile(
            request,
            str(prior_result.get("providerReceiptRef", {}).get("recordId") or ""),
            permit_digest=self._permit_digest,
        )
        state = str(response.get("attemptState") or "")
        result = {
            "confirmed": "confirmed",
            "reconciled_succeeded": "confirmed",
            "cancelled": "cancelled",
            "reconciled_failed": "failed",
        }.get(state, "still_unknown")
        observation_id = str(response.get("providerReceiptId") or "")
        if not observation_id:
            raise ValueError("calendar reconciliation requires a provider observation")
        provider_response = response.get("providerResponse")
        provider_appointment_ref = (
            provider_response.get("appointmentRef") if isinstance(provider_response, dict) else None
        )
        appointment_ref = (
            response.get("appointmentRef") or provider_appointment_ref
            if result in {"confirmed", "cancelled"}
            else None
        )
        return {
            "messageType": "booking_reconciliation",
            "schemaVersion": "availability-booking/1.0.0",
            "tenantId": prior_result["tenantId"],
            "reconciliationId": _stable_id(
                "booking-reconciliation", prior_result["resultId"], observation_id
            ),
            "commandRef": prior_result["commandRef"],
            "priorResultRef": {
                "recordId": prior_result["resultId"],
                "recordType": "BookingResult",
                "version": 1,
            },
            "providerBindingRef": prior_result["providerBindingRef"],
            "providerObservationRef": {
                "recordId": observation_id,
                "recordType": "ProviderObservation",
                "version": 1,
            },
            "result": result,
            "appointmentRef": appointment_ref,
            "appointmentVersion": (
                int(appointment_ref["version"]) if appointment_ref is not None else None
            ),
            "derivedAt": _now(),
            "derivedBy": {
                "principalId": "calendar-connector",
                "implementationId": "booking_reconciliation_v1",
                "implementationVersion": "calendar-operations/1.0.0",
            },
            "reasonCodes": [f"provider_{result}"],
            "evidenceIds": [observation_id],
        }

    def _execute(
        self, command: dict[str, Any], action: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        raw_payload = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        request = self._request_for(command, action, raw_payload)
        response = self._invoker(
            request,
            raw_payload,
            permit_digest=self._permit_digest,
            preview=None,
        )
        outcome = str(response.get("outcome") or "")
        state = {
            "confirmed": "confirmed",
            "rejected": "rejected",
            "unknown": "unknown_outcome",
            "conflict": "calendar_conflict",
            "revoked": "rejected",
        }.get(outcome)
        if state is None:
            raise ValueError("connector response has no supported calendar outcome")
        receipt = str(response.get("receiptId") or request.get("requestId") or "")
        if not receipt:
            raise ValueError("calendar connector response has no receipt")
        return {
            "messageType": "booking_result",
            "schemaVersion": "availability-booking/1.0.0",
            "tenantId": command["tenantId"],
            "resultId": _stable_id("booking-result", command["commandId"], receipt),
            "commandRef": {
                "recordId": command["commandId"],
                "recordType": "BookingCommand",
                "version": 1,
            },
            "state": state,
            "providerBindingRef": command["providerBindingRef"],
            "providerRequestId": str(request.get("requestId") or "") or None,
            "providerEventId": receipt if state == "confirmed" else None,
            "providerVersion": str(response.get("providerVersion") or "unknown"),
            "providerReceiptRef": {
                "recordId": receipt,
                "recordType": "ProviderReceipt",
                "version": 1,
            },
            "observedAt": _now(),
            "reasonCodes": [f"provider_{state}"],
            "evidenceIds": [receipt],
        }


class CalendarOperationService:
    """Persist the output of each validated calendar operation exactly once."""

    def __init__(
        self,
        provider: CalendarProvider,
        *,
        slot_sets: SlotSetRepository,
        outcomes: BookingOutcomeRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._runtime = CalendarRuntime(provider, clock=clock)
        self._slot_sets = slot_sets
        self._outcomes = outcomes

    def availability(
        self,
        *,
        policy: dict[str, Any],
        readiness: dict[str, Any],
        binding: dict[str, Any],
        snapshot: dict[str, Any],
        principal_id: str,
        location_options: Sequence[tuple[str, Sequence[str]]],
        blocked_intervals: Sequence[dict[str, str]] = (),
    ) -> dict[str, Any]:
        slot_set = self._runtime.derive_availability(
            policy,
            readiness,
            binding,
            snapshot,
            principal_id=principal_id,
            location_options=location_options,
            blocked_intervals=blocked_intervals,
        )
        self._slot_sets.append_slot_set(
            policy=policy,
            readiness=readiness,
            binding=binding,
            snapshot=snapshot,
            slot_set=slot_set,
        )
        return slot_set

    def snapshot(
        self, *, binding: dict[str, Any], range_start: str, range_end: str
    ) -> dict[str, Any]:
        snapshot = self._provider.snapshot(binding, range_start=range_start, range_end=range_end)
        self._slot_sets.append_calendar_snapshot(snapshot=snapshot)
        return snapshot

    def booking(
        self,
        *,
        command: dict[str, Any],
        binding: dict[str, Any],
        slot_set: dict[str, Any] | None,
        current_snapshot: dict[str, Any] | None,
        current_provider_watermark: str,
        current_appointment_version: int | None,
        authority_active: bool,
    ) -> dict[str, Any]:
        command_status = self._outcomes.append_booking_command(command=command)
        if command_status == "duplicate":
            existing = self._outcomes.get_booking_result(command_id=command["commandId"])
            if existing is not None:
                return existing
        result = self._runtime.book(
            command,
            binding=binding,
            slot_set=slot_set,
            current_snapshot=current_snapshot,
            current_provider_watermark=current_provider_watermark,
            current_appointment_version=current_appointment_version,
            authority_active=authority_active,
        )
        self._outcomes.append_booking_result(command=command, binding=binding, result=result)
        return result

    def reconciliation(
        self,
        *,
        command: dict[str, Any],
        binding: dict[str, Any],
        prior_result: dict[str, Any],
    ) -> dict[str, Any]:
        reconciliation = self._runtime.reconcile(prior_result)
        self._outcomes.append_booking_reconciliation(
            command=command,
            binding=binding,
            prior_result=prior_result,
            reconciliation=reconciliation,
        )
        return reconciliation


def _calendar_payload(command: dict[str, Any], slot: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": "calendar.book" if command["commandKind"] == "book" else "calendar.reschedule",
        "calendarId": command["providerBindingRef"]["recordId"],
        "id": (command.get("appointmentRef") or {}).get("recordId"),
        "start": slot["startsAt"],
        "end": slot["endsAt"],
        "timeZone": slot["timeZone"],
        "locationId": slot["locationId"],
        "journeyRef": command["journeyRef"],
    }


def _busy_intervals(provider_response: Any) -> list[dict[str, str]]:
    """Normalize Google free/busy, Graph schedule items, and adapter-neutral reads."""
    if not isinstance(provider_response, dict):
        return []
    candidates: list[Any] = []
    direct = provider_response.get("busyIntervals")
    if isinstance(direct, list):
        candidates.extend(direct)
    calendars = provider_response.get("calendars")
    if isinstance(calendars, dict):
        for calendar in calendars.values():
            if isinstance(calendar, dict) and isinstance(calendar.get("busy"), list):
                candidates.extend(calendar["busy"])
    schedules = provider_response.get("value")
    if isinstance(schedules, list):
        for schedule in schedules:
            if not isinstance(schedule, dict):
                continue
            items = schedule.get("scheduleItems")
            if isinstance(items, list):
                candidates.extend(items)
    result: list[dict[str, str]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        start = item.get("startsAt") or item.get("start")
        end = item.get("endsAt") or item.get("end")
        if isinstance(start, dict):
            start = start.get("dateTime")
        if isinstance(end, dict):
            end = end.get("dateTime")
        if isinstance(start, str) and isinstance(end, str):
            result.append({"startsAt": start, "endsAt": end})
    return sorted(result, key=lambda item: (item["startsAt"], item["endsAt"]))


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]
    return f"{prefix}:{digest}"


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
