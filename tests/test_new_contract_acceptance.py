import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from buyer_ops_contracts.contract_acceptance import (
    ContractSemanticError,
    admit_idempotency,
    canonical_digest,
    local_window_instants,
    readiness_result,
    select_next_question,
    validate_availability_policy,
    validate_booking_command,
    validate_booking_context,
    validate_calendar_snapshot,
    validate_policy_version,
    validate_qualification,
    validate_reconciliation,
    validate_slot_set,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def _load(relative: str) -> dict[str, Any]:
    return json.loads((FIXTURES / relative).read_text())


def _replace(document: dict[str, Any], path: str, value: Any) -> None:
    target: Any = document
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    final = parts[-1]
    if isinstance(target, list):
        target[int(final)] = value
    else:
        target[final] = value


@pytest.mark.parametrize(
    ("schema_name", "fixture_name"),
    [
        ("QUALIFICATION-READINESS.schema.json", "qualification_readiness/valid.json"),
        ("AVAILABILITY-BOOKING.schema.json", "availability_booking/valid.json"),
    ],
)
def test_every_valid_record_has_schema_acceptance(schema_name: str, fixture_name: str) -> None:
    schema = json.loads((ROOT / schema_name).read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for name, record in _load(fixture_name).items():
        errors = sorted(validator.iter_errors(record), key=lambda error: list(error.path))
        assert not errors, f"{name}: {errors}"


def test_qualification_semantic_fixture_matrix() -> None:
    valid = _load("qualification_readiness/valid.json")
    invalid = _load("qualification_readiness/invalid.json")
    for case in invalid["semanticCases"]:
        policy = copy.deepcopy(valid["policy"])
        inputs = copy.deepcopy(valid["input"])
        target = policy if case["target"] == "policy" else inputs
        if case["operation"] == "duplicate":
            target["criteria"].append(copy.deepcopy(target["criteria"][0]))
        else:
            _replace(target, case["path"], case["value"])
        with pytest.raises(ContractSemanticError, match=case["name"]):
            validate_qualification(policy, inputs)


def test_question_tie_break_and_readiness_are_order_independent() -> None:
    valid = _load("qualification_readiness/valid.json")
    policy = valid["policy"]
    inputs = copy.deepcopy(valid["input"])
    inputs["observations"] = []
    inputs["inputDigest"] = canonical_digest(
        {key: inputs[key] for key in sorted(inputs) if key not in {"inputDigest", "inputSetId"}}
    )
    assert select_next_question(policy, inputs) == ("ask", "timeframe")
    assert readiness_result(policy, inputs) == ("not_ready", ["budget", "timeframe"])
    reversed_policy = copy.deepcopy(policy)
    reversed_policy["criteria"].reverse()
    assert select_next_question(reversed_policy, inputs) == ("ask", "timeframe")


def test_decline_freshness_contradiction_and_digest_are_deterministic() -> None:
    valid = _load("qualification_readiness/valid.json")
    policy = valid["policy"]
    inputs = valid["input"]
    assert readiness_result(policy, inputs) == ("ready", [])
    assert inputs["inputDigest"] == canonical_digest(
        {key: inputs[key] for key in sorted(inputs) if key not in {"inputDigest", "inputSetId"}}
    )
    contradicted = copy.deepcopy(inputs)
    contradicted["observations"][0]["contradictionRefs"] = [
        {"recordId": "contradiction-a", "recordType": "Contradiction", "version": 1}
    ]
    contradicted["observations"][0]["validAtEvaluation"] = False
    contradicted["inputDigest"] = canonical_digest(
        {
            key: contradicted[key]
            for key in sorted(contradicted)
            if key not in {"inputDigest", "inputSetId"}
        }
    )
    assert select_next_question(policy, contradicted) == ("ask", "budget")
    assert readiness_result(policy, contradicted) == ("not_ready", ["budget"])


def test_policy_blocking_optional_contradiction_blocks_readiness() -> None:
    valid = _load("qualification_readiness/valid.json")
    policy = copy.deepcopy(valid["policy"])
    policy["criteria"].append(
        {
            "criterionId": "optional-risk",
            "predicate": "optional_risk_recorded",
            "disposition": "optional",
            "acceptedObservationStates": ["asserted", "verified"],
            "maxAgeSeconds": 2592000,
            "priority": 20,
            "questionTemplateRef": {
                "recordId": "question-optional-risk",
                "recordType": "QuestionTemplate",
                "version": 1,
            },
            "missingDisposition": "ignore",
            "contradictionDisposition": "block_readiness",
        }
    )
    inputs = copy.deepcopy(valid["input"])
    inputs["observations"].append(
        {
            "criterionId": "optional-risk",
            "observationRef": {
                "recordId": "obs-optional-risk",
                "recordType": "Assertion",
                "version": 1,
            },
            "observationState": "contradicted",
            "observedAt": "2026-02-28T12:00:00Z",
            "validAtEvaluation": False,
            "contradictionRefs": [
                {
                    "recordId": "contradiction-optional-risk",
                    "recordType": "Contradiction",
                    "version": 1,
                }
            ],
        }
    )
    inputs["inputDigest"] = canonical_digest(
        {key: inputs[key] for key in sorted(inputs) if key not in {"inputDigest", "inputSetId"}}
    )

    assert readiness_result(policy, inputs) == ("blocked", ["optional-risk"])


def test_policy_supersession_is_contiguous_and_tenant_bound() -> None:
    current = _load("qualification_readiness/valid.json")["policy"]
    validate_policy_version(current, None)
    successor = copy.deepcopy(current)
    successor["version"] = 2
    successor["supersedesRecordId"] = current["policyId"]
    validate_policy_version(successor, current)
    successor["version"] = 3
    with pytest.raises(ContractSemanticError, match="non_contiguous_policy_version"):
        validate_policy_version(successor, current)


def test_availability_semantic_fixture_matrix() -> None:
    valid = _load("availability_booking/valid.json")
    invalid = _load("availability_booking/invalid.json")
    for case in invalid["semanticCases"]:
        records = copy.deepcopy(valid)
        target = records[case["target"]]
        if case["operation"] == "replace":
            _replace(target, case["path"], case["value"])
        with pytest.raises(ContractSemanticError, match=case["name"]):
            if case["name"] == "unknown_time_zone":
                validate_availability_policy(records["policy"])
            elif case["name"] == "non_positive_snapshot_range":
                validate_calendar_snapshot(records["snapshot"])
            elif case["name"] == "cancel_forbids_slot":
                validate_booking_command(records["command"])
            elif case["name"] == "stale_provider_watermark":
                validate_booking_context(
                    records["command"],
                    binding=records["binding"],
                    current_provider_watermark=case["value"],
                    current_appointment_version=None,
                    authority_active=True,
                )
            else:
                validate_reconciliation(records["result"], records["reconciliation"])


def test_dst_resolution_slot_identity_and_expiry() -> None:
    valid = _load("availability_booking/valid.json")
    windows = local_window_instants(valid["policy"], date(2026, 3, 8))
    assert len(windows) == 1
    assert (windows[0][1] - windows[0][0]).total_seconds() == 7200
    validate_slot_set(valid["slotSet"], valid["policy"])
    stale = copy.deepcopy(valid["slotSet"])
    stale["expiresAt"] = "2026-03-08T08:10:01Z"
    with pytest.raises(ContractSemanticError, match="invalid_slot_set_expiry"):
        validate_slot_set(stale, valid["policy"])


def test_authority_watermark_version_idempotency_and_reconciliation() -> None:
    valid = _load("availability_booking/valid.json")
    command = valid["command"]
    validate_booking_context(
        command,
        binding=valid["binding"],
        current_provider_watermark="watermark-42",
        current_appointment_version=None,
        authority_active=True,
    )
    assert admit_idempotency(command, None) == "new"
    assert admit_idempotency(command, command["payloadDigest"]) == "duplicate"
    with pytest.raises(ContractSemanticError, match="idempotency_key_payload_conflict"):
        admit_idempotency(command, "sha256:ffffffffffffffffffffffffffffffff")
    validate_reconciliation(valid["result"], valid["reconciliation"])


def test_revocation_race_and_concurrent_version_fail_closed() -> None:
    valid = _load("availability_booking/valid.json")
    suspended = copy.deepcopy(valid["binding"])
    suspended["lifecycle"] = "revoked"
    with pytest.raises(ContractSemanticError, match="provider_binding_not_active"):
        validate_booking_context(
            valid["command"],
            binding=suspended,
            current_provider_watermark="watermark-42",
            current_appointment_version=None,
            authority_active=True,
        )
    reschedule = copy.deepcopy(valid["command"])
    reschedule["commandKind"] = "reschedule"
    reschedule["appointmentRef"] = {
        "recordId": "appointment-a",
        "recordType": "Appointment",
        "version": 2,
    }
    reschedule["expectedAppointmentVersion"] = 2
    with pytest.raises(ContractSemanticError, match="appointment_version_conflict"):
        validate_booking_context(
            reschedule,
            binding=valid["binding"],
            current_provider_watermark="watermark-42",
            current_appointment_version=3,
            authority_active=True,
        )
