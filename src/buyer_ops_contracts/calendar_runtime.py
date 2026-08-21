"""Governed availability, booking, and reconciliation runtime boundary."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from .contract_acceptance import (
    ContractSemanticError,
    derive_slot_set,
    require_unknown_outcome_resolution,
    validate_booking_context,
    validate_booking_result_context,
    validate_reconciliation,
)


class CalendarProvider(Protocol):
    """Provider transport; credentials and provider-specific fields stay behind it."""

    def book(self, command: dict[str, Any], slot: dict[str, Any]) -> dict[str, Any]: ...

    def reschedule(self, command: dict[str, Any], slot: dict[str, Any]) -> dict[str, Any]: ...

    def cancel(self, command: dict[str, Any]) -> dict[str, Any]: ...

    def reconcile(self, prior_result: dict[str, Any]) -> dict[str, Any]: ...

    def snapshot(
        self, binding: dict[str, Any], *, range_start: str, range_end: str
    ) -> dict[str, Any]: ...


class CalendarRuntime:
    """Compose policy derivation and provider execution without bypassing contracts."""

    def __init__(
        self, provider: CalendarProvider, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(UTC))

    def derive_availability(
        self,
        policy: dict[str, Any],
        readiness: dict[str, Any],
        binding: dict[str, Any],
        snapshot: dict[str, Any],
        *,
        principal_id: str,
        location_options: Sequence[tuple[str, Sequence[str]]],
        blocked_intervals: Sequence[dict[str, str]] = (),
    ) -> dict[str, Any]:
        return derive_slot_set(
            policy,
            readiness,
            binding,
            snapshot,
            derived_at=self._clock().astimezone(UTC),
            principal_id=principal_id,
            location_options=location_options,
            blocked_intervals=blocked_intervals,
        )

    def book(
        self,
        command: dict[str, Any],
        *,
        binding: dict[str, Any],
        slot_set: dict[str, Any] | None,
        current_snapshot: dict[str, Any] | None,
        current_provider_watermark: str,
        current_appointment_version: int | None,
        authority_active: bool,
    ) -> dict[str, Any]:
        evaluated_at = self._clock().astimezone(UTC)
        validate_booking_context(
            command,
            binding=binding,
            slot_set=slot_set,
            current_snapshot=current_snapshot,
            current_provider_watermark=current_provider_watermark,
            current_appointment_version=current_appointment_version,
            authority_active=authority_active,
            evaluated_at=evaluated_at,
        )
        selected_slot = None
        if slot_set is not None and command["selectedSlotId"] is not None:
            selected_slot = next(
                slot
                for slot in slot_set["slots"]
                if slot["slotId"] == command["selectedSlotId"]
                and slot["slotDigest"] == command["selectedSlotDigest"]
            )
        if command["commandKind"] == "cancel":
            result = self._provider.cancel(command)
        else:
            if selected_slot is None:
                raise ContractSemanticError("selected_slot_mismatch")
            if command["commandKind"] == "reschedule":
                result = self._provider.reschedule(command, selected_slot)
            else:
                result = self._provider.book(command, selected_slot)
        validate_booking_result_context(command=command, result=result, binding=binding)
        return result

    def reconcile(self, prior_result: dict[str, Any]) -> dict[str, Any]:
        if prior_result.get("state") != "unknown_outcome":
            raise ContractSemanticError("reconciliation_requires_unknown_outcome")
        reconciliation = self._provider.reconcile(prior_result)
        validate_reconciliation(prior_result, reconciliation)
        require_unknown_outcome_resolution(prior_result, reconciliation)
        return reconciliation
