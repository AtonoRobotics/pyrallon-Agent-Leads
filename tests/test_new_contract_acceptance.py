import copy
import json
from datetime import UTC, date, datetime
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
    require_unknown_outcome_resolution,
    select_next_question,
    validate_availability_policy,
    validate_booking_command,
    validate_booking_context,
    validate_booking_result_context,
    validate_calendar_snapshot,
    validate_policy_version,
    validate_qualification,
    validate_qualification_decisions,
    validate_reconciliation,
    validate_slot_set,
    validate_slot_set_context,
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


def test_qualification_inputs_bind_the_exact_policy_version() -> None:
    valid = _load("qualification_readiness/valid.json")
    policy = valid["policy"]
    inputs = copy.deepcopy(valid["input"])
    inputs["policyRef"]["version"] = policy["version"] + 1
    inputs["inputDigest"] = canonical_digest(
        {key: inputs[key] for key in sorted(inputs) if key not in {"inputDigest", "inputSetId"}}
    )

    with pytest.raises(ContractSemanticError, match="input_policy_reference_mismatch"):
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


def test_buyer_decline_does_not_satisfy_a_required_criterion() -> None:
    valid = _load("qualification_readiness/valid.json")
    policy = copy.deepcopy(valid["policy"])
    inputs = copy.deepcopy(valid["input"])
    required = policy["criteria"][0]
    required["acceptedObservationStates"].append("buyer_declined")
    observation = inputs["observations"][0]
    observation["observationState"] = "buyer_declined"
    inputs["inputDigest"] = canonical_digest(
        {key: inputs[key] for key in sorted(inputs) if key not in {"inputDigest", "inputSetId"}}
    )

    assert select_next_question(policy, inputs) == ("ask", required["criterionId"])
    assert readiness_result(policy, inputs) == ("not_ready", [required["criterionId"]])


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


@pytest.mark.parametrize(
    ("evaluated_at", "effective_to"),
    [
        ("2025-12-31T23:59:59Z", None),
        ("2026-03-01T12:00:00Z", "2026-03-01T12:00:00Z"),
        ("2026-03-01T12:00:01Z", "2026-03-01T12:00:00Z"),
    ],
)
def test_only_policy_versions_in_the_half_open_effective_interval_apply(
    evaluated_at: str, effective_to: str | None
) -> None:
    valid = _load("qualification_readiness/valid.json")
    policy = copy.deepcopy(valid["policy"])
    policy["effectiveTo"] = effective_to
    inputs = copy.deepcopy(valid["input"])
    inputs["evaluatedAt"] = evaluated_at

    with pytest.raises(ContractSemanticError, match="policy_not_effective"):
        validate_qualification(policy, inputs)


def test_qualification_decisions_bind_exact_inputs_and_deterministic_results() -> None:
    valid = _load("qualification_readiness/valid.json")
    validate_qualification_decisions(
        valid["policy"], valid["input"], valid["nextQuestion"], valid["readiness"]
    )

    wrong_digest = copy.deepcopy(valid["nextQuestion"])
    wrong_digest["inputDigest"] = "sha256:00000000000000000000000000000000"
    with pytest.raises(ContractSemanticError, match="decision_input_digest_mismatch"):
        validate_qualification_decisions(
            valid["policy"], valid["input"], wrong_digest, valid["readiness"]
        )

    wrong_result = copy.deepcopy(valid["readiness"])
    wrong_result["result"] = "not_ready"
    with pytest.raises(ContractSemanticError, match="readiness_result_mismatch"):
        validate_qualification_decisions(
            valid["policy"], valid["input"], valid["nextQuestion"], wrong_result
        )


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
                    slot_set=records["slotSet"],
                    current_provider_watermark=case["value"],
                    current_appointment_version=None,
                    authority_active=True,
                    evaluated_at=datetime(2026, 3, 8, 8, 1, tzinfo=UTC),
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


def test_slot_set_binds_current_readiness_policy_binding_and_snapshot() -> None:
    booking = _load("availability_booking/valid.json")
    readiness = copy.deepcopy(_load("qualification_readiness/valid.json")["readiness"])
    readiness["expiresAt"] = "2026-03-08T08:05:00Z"
    validate_slot_set_context(
        booking["slotSet"],
        policy=booking["policy"],
        readiness=readiness,
        binding=booking["binding"],
        snapshot=booking["snapshot"],
    )

    wrong_binding = copy.deepcopy(booking["slotSet"])
    wrong_binding["providerBindingRef"]["version"] = 2
    with pytest.raises(ContractSemanticError, match="slot_set_binding_reference_mismatch"):
        validate_slot_set_context(
            wrong_binding,
            policy=booking["policy"],
            readiness=readiness,
            binding=booking["binding"],
            snapshot=booking["snapshot"],
        )

    expired_readiness = copy.deepcopy(readiness)
    expired_readiness["expiresAt"] = booking["slotSet"]["derivedAt"]
    with pytest.raises(ContractSemanticError, match="readiness_not_current"):
        validate_slot_set_context(
            booking["slotSet"],
            policy=booking["policy"],
            readiness=expired_readiness,
            binding=booking["binding"],
            snapshot=booking["snapshot"],
        )


def test_derived_records_bind_their_assigned_implementations() -> None:
    booking = _load("availability_booking/valid.json")
    readiness = copy.deepcopy(_load("qualification_readiness/valid.json")["readiness"])
    readiness["expiresAt"] = "2026-03-08T08:05:00Z"
    wrong_slot_deriver = copy.deepcopy(booking["slotSet"])
    wrong_slot_deriver["derivedBy"]["implementationId"] = "booking_reconciliation_v1"

    with pytest.raises(ContractSemanticError, match="slot_set_deriver_mismatch"):
        validate_slot_set_context(
            wrong_slot_deriver,
            policy=booking["policy"],
            readiness=readiness,
            binding=booking["binding"],
            snapshot=booking["snapshot"],
        )

    wrong_reconciliation_deriver = copy.deepcopy(booking["reconciliation"])
    wrong_reconciliation_deriver["derivedBy"]["implementationId"] = "availability_v1"
    with pytest.raises(ContractSemanticError, match="reconciliation_deriver_mismatch"):
        validate_reconciliation(booking["result"], wrong_reconciliation_deriver)


def test_slot_set_rejects_wrong_consultation_duration() -> None:
    booking = _load("availability_booking/valid.json")
    readiness = copy.deepcopy(_load("qualification_readiness/valid.json")["readiness"])
    readiness["expiresAt"] = "2026-03-08T08:05:00Z"
    slot = booking["slotSet"]["slots"][0]
    slot["endsAt"] = "2026-03-08T09:45:00Z"
    slot_payload = {key: slot[key] for key in sorted(slot) if key not in {"slotId", "slotDigest"}}
    slot["slotDigest"] = canonical_digest(slot_payload)
    slot["slotId"] = slot["slotDigest"].split(":", 1)[1]

    with pytest.raises(ContractSemanticError, match="slot_duration_mismatch"):
        validate_slot_set_context(
            booking["slotSet"],
            policy=booking["policy"],
            readiness=readiness,
            binding=booking["binding"],
            snapshot=booking["snapshot"],
        )


def test_authority_watermark_version_idempotency_and_reconciliation() -> None:
    valid = _load("availability_booking/valid.json")
    command = valid["command"]
    validate_booking_context(
        command,
        binding=valid["binding"],
        slot_set=valid["slotSet"],
        current_provider_watermark="watermark-42",
        current_appointment_version=None,
        authority_active=True,
        evaluated_at=datetime(2026, 3, 8, 8, 1, tzinfo=UTC),
    )
    assert admit_idempotency(command, None) == "new"
    assert admit_idempotency(command, command["payloadDigest"]) == "duplicate"
    with pytest.raises(ContractSemanticError, match="idempotency_key_payload_conflict"):
        admit_idempotency(command, "sha256:ffffffffffffffffffffffffffffffff")
    validate_reconciliation(valid["result"], valid["reconciliation"])

    wrong_prior = copy.deepcopy(valid["reconciliation"])
    wrong_prior["priorResultRef"]["recordId"] = "result-other"
    with pytest.raises(ContractSemanticError, match="prior_result_reference_mismatch"):
        validate_reconciliation(valid["result"], wrong_prior)

    missing_observation_evidence = copy.deepcopy(valid["reconciliation"])
    missing_observation_evidence["evidenceIds"] = ["request-a"]
    with pytest.raises(ContractSemanticError, match="provider_observation_evidence_missing"):
        validate_reconciliation(valid["result"], missing_observation_evidence)


def test_unknown_outcome_barrier_requires_source_linked_terminal_truth() -> None:
    valid = _load("availability_booking/valid.json")
    prior_result = valid["result"]
    reconciliation = valid["reconciliation"]

    with pytest.raises(ContractSemanticError, match="reconciliation_required"):
        require_unknown_outcome_resolution(prior_result, None)
    assert require_unknown_outcome_resolution(prior_result, reconciliation) == "confirmed"
    cancelled = copy.deepcopy(reconciliation)
    cancelled["result"] = "cancelled"
    assert require_unknown_outcome_resolution(prior_result, cancelled) == "cancelled"

    still_unknown = copy.deepcopy(reconciliation)
    still_unknown["result"] = "still_unknown"
    still_unknown["appointmentRef"] = None
    still_unknown["appointmentVersion"] = None
    with pytest.raises(ContractSemanticError, match="reconciliation_required"):
        require_unknown_outcome_resolution(prior_result, still_unknown)

    conflicted = copy.deepcopy(still_unknown)
    conflicted["result"] = "conflict_requires_resolution"
    with pytest.raises(ContractSemanticError, match="reconciliation_required"):
        require_unknown_outcome_resolution(prior_result, conflicted)
    mismatched_unknown = copy.deepcopy(still_unknown)
    mismatched_unknown["priorResultRef"]["recordId"] = "result-other"
    with pytest.raises(ContractSemanticError, match="prior_result_reference_mismatch"):
        require_unknown_outcome_resolution(prior_result, mismatched_unknown)

    failed = copy.deepcopy(still_unknown)
    failed["result"] = "failed"
    assert require_unknown_outcome_resolution(prior_result, failed) == "failed"


@pytest.mark.parametrize(
    ("target", "path", "value", "error"),
    [
        ("result", "tenantId", "tenant-other", "cross_tenant_reference"),
        ("result", "commandRef.recordId", "command-other", "result_command_reference_mismatch"),
        ("result", "commandRef.recordType", "OtherCommand", "result_command_reference_mismatch"),
        ("result", "commandRef.version", 2, "result_command_reference_mismatch"),
        (
            "result",
            "providerBindingRef.version",
            2,
            "result_binding_reference_mismatch",
        ),
        (
            "command",
            "providerBindingRef.recordId",
            "binding-other",
            "command_binding_reference_mismatch",
        ),
    ],
)
def test_booking_result_binds_exact_command_and_provider_context(
    target: str, path: str, value: Any, error: str
) -> None:
    valid = _load("availability_booking/valid.json")
    command = copy.deepcopy(valid["command"])
    result = copy.deepcopy(valid["result"])
    binding = valid["binding"]
    validate_booking_result_context(command=command, result=result, binding=binding)
    _replace(command if target == "command" else result, path, value)

    with pytest.raises(ContractSemanticError, match=error):
        validate_booking_result_context(command=command, result=result, binding=binding)


def test_revocation_race_and_concurrent_version_fail_closed() -> None:
    valid = _load("availability_booking/valid.json")
    suspended = copy.deepcopy(valid["binding"])
    suspended["lifecycle"] = "revoked"
    with pytest.raises(ContractSemanticError, match="provider_binding_not_active"):
        validate_booking_context(
            valid["command"],
            binding=suspended,
            slot_set=valid["slotSet"],
            current_provider_watermark="watermark-42",
            current_appointment_version=None,
            authority_active=True,
            evaluated_at=datetime(2026, 3, 8, 8, 1, tzinfo=UTC),
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
            slot_set=valid["slotSet"],
            current_provider_watermark="watermark-42",
            current_appointment_version=3,
            authority_active=True,
            evaluated_at=datetime(2026, 3, 8, 8, 1, tzinfo=UTC),
        )


def test_booking_command_rejects_unbound_or_expired_selected_slot() -> None:
    valid = _load("availability_booking/valid.json")
    wrong_slot = copy.deepcopy(valid["command"])
    wrong_slot["selectedSlotDigest"] = "sha256:ffffffffffffffffffffffffffffffff"
    with pytest.raises(ContractSemanticError, match="selected_slot_mismatch"):
        validate_booking_context(
            wrong_slot,
            binding=valid["binding"],
            slot_set=valid["slotSet"],
            current_provider_watermark="watermark-42",
            current_appointment_version=None,
            authority_active=True,
            evaluated_at=datetime(2026, 3, 8, 8, 1, tzinfo=UTC),
        )

    with pytest.raises(ContractSemanticError, match="slot_set_expired"):
        validate_booking_context(
            valid["command"],
            binding=valid["binding"],
            slot_set=valid["slotSet"],
            current_provider_watermark="watermark-42",
            current_appointment_version=None,
            authority_active=True,
            evaluated_at=datetime(2026, 3, 8, 8, 10, tzinfo=UTC),
        )
