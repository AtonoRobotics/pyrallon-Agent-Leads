import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from buyer_ops_contracts.calendar_operations import (
    CalendarOperationService,
    ConnectorCalendarProvider,
)
from buyer_ops_contracts.calendar_runtime import CalendarRuntime
from buyer_ops_contracts.contract_acceptance import ContractSemanticError
from buyer_ops_contracts.structural import validate_record

ROOT = Path(__file__).resolve().parents[1]


def _fixture(name: str) -> dict:
    return json.loads((ROOT / "tests" / "fixtures" / "valid" / name).read_text())


class _Provider:
    def __init__(self, result: dict):
        self.result = result
        self.calls = []

    def reschedule(self, command, slot):
        self.calls.append(("reschedule", command, slot))
        return copy.deepcopy(self.result)

    def book(self, command, slot):
        self.calls.append(("book", command, slot))
        return copy.deepcopy(self.result)

    def cancel(self, command):
        self.calls.append(("cancel", command))
        return copy.deepcopy(self.result)

    def reconcile(self, prior_result):
        self.calls.append(("reconcile", prior_result))
        return copy.deepcopy(self.result)


def test_calendar_runtime_derives_deterministic_slots():
    calendar_fixture = _fixture("../availability_booking/valid.json")
    calendar_fixture["readiness"] = {
        "tenantId": calendar_fixture["slotSet"]["tenantId"],
        "decisionId": "readiness-a",
        "journeyRef": calendar_fixture["slotSet"]["journeyRef"],
        "result": "ready",
        "derivedAt": calendar_fixture["slotSet"]["derivedAt"],
        "expiresAt": calendar_fixture["slotSet"]["expiresAt"],
    }
    provider = _Provider({})
    runtime = CalendarRuntime(
        provider,
        clock=lambda: datetime(2026, 3, 8, 8, 0, tzinfo=UTC),
    )
    slot_set = runtime.derive_availability(
        calendar_fixture["policy"],
        calendar_fixture["readiness"],
        calendar_fixture["binding"],
        calendar_fixture["snapshot"],
        principal_id="operator-1",
        location_options=(("office", ("room-1",)),),
    )
    assert slot_set["derivedBy"]["implementationId"] == "availability_v1"
    assert slot_set["slots"]


def test_calendar_runtime_refuses_booking_without_active_authority():
    calendar_fixture = _fixture("../availability_booking/valid.json")
    runtime = CalendarRuntime(_Provider({}))
    with pytest.raises(ContractSemanticError, match="authority_not_active"):
        runtime.book(
            calendar_fixture["command"],
            binding=calendar_fixture["binding"],
            slot_set=calendar_fixture["slotSet"],
            current_snapshot=calendar_fixture["snapshot"],
            current_provider_watermark="watermark-1",
            current_appointment_version=None,
            authority_active=False,
        )


def test_calendar_runtime_dispatches_reschedule_to_provider_mutation():
    calendar_fixture = _fixture("../availability_booking/valid.json")
    command = copy.deepcopy(calendar_fixture["command"])
    command.update(
        {
            "commandKind": "reschedule",
            "commandId": "reschedule-command-a",
            "appointmentRef": {
                "recordId": "appointment-a",
                "recordType": "Appointment",
                "version": 1,
            },
            "expectedAppointmentVersion": 1,
        }
    )
    result = copy.deepcopy(calendar_fixture["result"])
    result["commandRef"] = {
        "recordId": command["commandId"],
        "recordType": "BookingCommand",
        "version": 1,
    }
    provider = _Provider(result)
    runtime = CalendarRuntime(provider, clock=lambda: datetime(2026, 3, 8, 8, 0, tzinfo=UTC))

    runtime.book(
        command,
        binding=calendar_fixture["binding"],
        slot_set=calendar_fixture["slotSet"],
        current_snapshot=calendar_fixture["snapshot"],
        current_provider_watermark="watermark-42",
        current_appointment_version=1,
        authority_active=True,
    )

    assert provider.calls[0][0] == "reschedule"


class _Invoker:
    def __init__(self, response: dict):
        self.response = response
        self.calls = []

    def __call__(self, request, payload, *, permit_digest, preview):
        self.calls.append((request, json.loads(payload), permit_digest, preview))
        return copy.deepcopy(self.response)

    def reconcile(self, request, provider_receipt_id, *, permit_digest):
        self.calls.append((request, provider_receipt_id, permit_digest))
        return {
            "attemptState": "reconciled_succeeded",
            "providerReceiptId": provider_receipt_id,
            "providerResponse": {"appointmentRef": {"recordId": "appointment-a", "version": 2}},
        }


class _AppendOnly:
    def __init__(self):
        self.records = []

    def append_slot_set(self, **records):
        self.records.append(("slot_set", records))

    def append_booking_result(self, **records):
        self.records.append(("booking_result", records))

    def append_booking_command(self, **records):
        self.records.append(("booking_command", records))
        return "new"

    def get_booking_result(self, **records):
        return None

    def append_booking_reconciliation(self, **records):
        self.records.append(("booking_reconciliation", records))


def test_connector_calendar_provider_maps_governed_receipt_to_contract_result():
    fixture = _fixture("../availability_booking/valid.json")
    command = fixture["command"]
    invoker = _Invoker(
        {"outcome": "confirmed", "receiptId": "provider-event-1", "providerVersion": "v1"}
    )
    provider = ConnectorCalendarProvider(
        invoker,
        request_for=lambda record, action, raw: {
            "requestId": "provider-request-1",
            "capability": action,
        },
        permit_digest="permit-1",
    )
    result = provider.book(command, fixture["slotSet"]["slots"][0])

    validate_record(result, "availability_booking")
    assert result["state"] == "confirmed"
    assert result["providerEventId"] == "provider-event-1"
    assert invoker.calls[0][2] == "permit-1"
    assert invoker.calls[0][1]["action"] == "calendar.book"


def test_connector_calendar_provider_normalizes_provider_availability_snapshot():
    fixture = _fixture("../availability_booking/valid.json")
    invoker = _Invoker(
        {
            "outcome": "confirmed",
            "receiptId": "provider-read-1",
            "providerVersion": "v1",
            "providerResponse": {
                "calendars": {
                    "calendar-a": {
                        "busy": [{"start": "2026-03-08T10:00:00Z", "end": "2026-03-08T11:00:00Z"}]
                    }
                }
            },
        }
    )
    provider = ConnectorCalendarProvider(
        invoker,
        request_for=lambda record, action, raw: {
            "requestId": "provider-request-1",
            "capability": "read",
        },
        permit_digest="permit-1",
    )
    snapshot = provider.snapshot(
        fixture["binding"],
        range_start="2026-03-08T08:00:00Z",
        range_end="2026-03-22T07:00:00Z",
    )
    validate_record(snapshot, "availability_booking")
    assert snapshot["busyIntervals"] == [
        {"startsAt": "2026-03-08T10:00:00Z", "endsAt": "2026-03-08T11:00:00Z"}
    ]


def test_calendar_operation_service_persists_validated_availability_and_booking():
    fixture = _fixture("../availability_booking/valid.json")
    qualification = _fixture("../qualification_readiness/valid.json")
    readiness = qualification["readiness"]
    readiness["expiresAt"] = "2026-03-08T08:05:00Z"
    append_only = _AppendOnly()
    provider_result = copy.deepcopy(fixture["result"])
    provider_result["state"] = "confirmed"
    provider_result["providerEventId"] = "provider-event-1"
    provider = _Provider(provider_result)
    service = CalendarOperationService(
        provider,
        slot_sets=append_only,
        outcomes=append_only,
        clock=lambda: datetime(2026, 3, 8, 8, 0, tzinfo=UTC),
    )
    slot_set = service.availability(
        policy=fixture["policy"],
        readiness=readiness,
        binding=fixture["binding"],
        snapshot=fixture["snapshot"],
        principal_id="operator-1",
        location_options=(("office", ("room-1",)),),
    )
    assert slot_set["slots"]
    assert append_only.records[0][0] == "slot_set"

    result = service.booking(
        command=fixture["command"],
        binding=fixture["binding"],
        slot_set=fixture["slotSet"],
        current_snapshot=fixture["snapshot"],
        current_provider_watermark="watermark-42",
        current_appointment_version=None,
        authority_active=True,
    )
    assert result["state"] == "confirmed"
    assert append_only.records[1][0] == "booking_command"
    assert append_only.records[2][0] == "booking_result"
