"""Run the deployed calendar and e-signature provider lifecycle end to end.

This runner intentionally has no fixture defaults.  The payloads are published
contract records from the target tenant and the permit is redeemed by Habitat.
It therefore fails closed when the deployment is not configured for real
provider effects instead of silently substituting a fake adapter.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


def _post(
    base: str,
    path: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=body,
        method="POST",
        headers={**headers, "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            decoded = json.loads(raw.decode()) if raw else {}
            return int(response.status), decoded if isinstance(decoded, dict) else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            decoded = json.loads(raw.decode()) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded = {"raw": raw.decode(errors="replace")}
        return int(exc.code), decoded if isinstance(decoded, dict) else {}


def _required_json(name: str) -> dict[str, Any]:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for provider-backed calendar E2E")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{name} must be valid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(f"{name} must contain an object")
    return decoded


def _require_provider_receipt(response: dict[str, Any], label: str) -> str:
    value: Any = response
    for key in ("bookingResult", "eSignature"):
        if isinstance(value, dict) and key in value:
            value = value[key]
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} response did not contain a normalized provider result")
    receipt = value.get("providerReceiptRef") or value.get("providerEnvelopeId")
    if isinstance(receipt, dict):
        receipt = receipt.get("recordId")
    if not isinstance(receipt, str) or not receipt:
        raise RuntimeError(f"{label} response did not contain a provider receipt")
    return receipt


def run(base: str) -> dict[str, Any]:
    token = os.environ.get("BUYER_OPS_CONTROL_TOKEN", "").strip()
    tenant = os.environ.get("BUYER_OPS_E2E_TENANT", "").strip()
    actor = os.environ.get("BUYER_OPS_E2E_ACTOR", "").strip()
    if not token or not tenant or not actor:
        raise RuntimeError(
            "BUYER_OPS_CONTROL_TOKEN, BUYER_OPS_E2E_TENANT, and BUYER_OPS_E2E_ACTOR are required"
        )

    base_headers = {
        "x-buyer-ops-token": token,
        "x-buyer-ops-tenant": tenant,
        "x-buyer-ops-actor": actor,
    }

    def headers(permit: str) -> dict[str, str]:
        if not permit:
            raise RuntimeError("Habitat did not return an effect permit")
        return {
            **base_headers,
            "x-buyer-ops-permit": permit,
        }

    def admit_effect(intent_name: str) -> str:
        intent = _required_json(intent_name)
        status, response = _post(
            base,
            "/v1/habitat/admit-event",
            intent,
            headers=base_headers,
        )
        permit = (response.get("permit") or {}).get("permit_digest")
        if status != 200 or response.get("allowed") is not True or not isinstance(permit, str):
            raise RuntimeError(f"{intent_name} was not admitted by Habitat: HTTP {status}")
        return permit

    availability = _required_json("BUYER_OPS_CALENDAR_E2E_AVAILABILITY_JSON")
    snapshot = _required_json("BUYER_OPS_CALENDAR_E2E_SNAPSHOT_JSON")
    booking = _required_json("BUYER_OPS_CALENDAR_E2E_BOOKING_JSON")
    reconciliation = _required_json("BUYER_OPS_CALENDAR_E2E_RECONCILE_JSON")
    esign_present = _required_json("BUYER_OPS_ESIGNATURE_E2E_PRESENT_JSON")
    esign_reconcile = _required_json("BUYER_OPS_ESIGNATURE_E2E_RECONCILE_JSON")
    snapshot_permit = admit_effect("BUYER_OPS_CALENDAR_E2E_SNAPSHOT_INTENT_JSON")
    booking_permit = admit_effect("BUYER_OPS_CALENDAR_E2E_BOOKING_INTENT_JSON")
    booking_replay_permit = admit_effect("BUYER_OPS_CALENDAR_E2E_BOOKING_REPLAY_INTENT_JSON")
    reconcile_permit = admit_effect("BUYER_OPS_CALENDAR_E2E_RECONCILE_INTENT_JSON")
    esign_present_permit = admit_effect("BUYER_OPS_ESIGNATURE_E2E_PRESENT_INTENT_JSON")
    esign_reconcile_permit = admit_effect("BUYER_OPS_ESIGNATURE_E2E_RECONCILE_INTENT_JSON")

    availability_status, availability_response = _post(
        base,
        "/v1/calendar/availability",
        availability,
        headers=base_headers,
    )
    snapshot_status, snapshot_response = _post(
        base,
        "/v1/calendar/snapshot",
        snapshot,
        headers=headers(snapshot_permit),
    )
    booking_status, booking_response = _post(
        base,
        "/v1/calendar/booking",
        booking,
        headers=headers(booking_permit),
    )
    booking_replay_status, booking_replay = _post(
        base,
        "/v1/calendar/booking",
        booking,
        headers=headers(booking_replay_permit),
    )
    reconciliation = {
        **reconciliation,
        "command": reconciliation.get("command", booking.get("command")),
        "priorResult": reconciliation.get("priorResult", booking_response.get("bookingResult")),
    }
    reconcile_status, reconcile_response = _post(
        base,
        "/v1/calendar/reconcile",
        reconciliation,
        headers=headers(reconcile_permit),
    )
    esign_status, esign_response = _post(
        base,
        "/v1/representation/esign/present",
        esign_present,
        headers=headers(esign_present_permit),
    )
    esign_replay_status, esign_replay = _post(
        base,
        "/v1/representation/esign/present",
        esign_present,
        headers=base_headers,
    )
    esign_reconcile = {
        **esign_reconcile,
        "agreementId": esign_reconcile.get("agreementId", esign_present.get("agreementId")),
        "providerEnvelopeId": esign_reconcile.get(
            "providerEnvelopeId",
            (esign_response.get("eSignature") or {}).get("providerEnvelopeId"),
        ),
    }
    esign_reconcile_status, esign_reconcile_response = _post(
        base,
        "/v1/representation/esign/reconcile",
        esign_reconcile,
        headers=headers(esign_reconcile_permit),
    )

    booking_receipt = _require_provider_receipt(booking_response, "booking")
    esign_receipt = _require_provider_receipt(esign_response, "e-signature")
    if booking_replay.get("duplicate") is not True:
        raise RuntimeError("booking replay did not return an explicit duplicate result")
    result = {
        "schemaVersion": "buyer-ops-calendar-esignature-e2e/1.0.0",
        "passed": all(
            status == 200
            for status in (
                availability_status,
                snapshot_status,
                booking_status,
                booking_replay_status,
                reconcile_status,
                esign_status,
                esign_replay_status,
                esign_reconcile_status,
            )
        ),
        "statuses": {
            "availability": availability_status,
            "snapshot": snapshot_status,
            "booking": booking_status,
            "bookingReplay": booking_replay_status,
            "reconcile": reconcile_status,
            "esignPresent": esign_status,
            "esignReplay": esign_replay_status,
            "esignReconcile": esign_reconcile_status,
        },
        "bookingReceipt": booking_receipt,
        "bookingReplayDuplicate": booking_replay["duplicate"],
        "esignReplayDuplicate": esign_replay.get("duplicate"),
        "esignatureEnvelope": esign_receipt,
        "providerResponsesPresent": all(
            isinstance(response, dict)
            for response in (
                availability_response,
                snapshot_response,
                booking_response,
                reconcile_response,
                esign_response,
                esign_reconcile_response,
            )
        ),
    }
    if esign_replay.get("duplicate") is not True:
        raise RuntimeError("e-signature replay did not return an explicit duplicate result")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8090")
    args = parser.parse_args()
    try:
        result = run(args.base)
    except (RuntimeError, OSError, urllib.error.URLError) as exc:
        print(f"calendar/e-signature provider E2E failed closed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
