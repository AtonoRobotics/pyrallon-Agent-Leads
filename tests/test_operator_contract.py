from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from buyer_ops_contracts.errors import ContractViolation
from buyer_ops_contracts.operator_commands import command_payload_digest
from buyer_ops_contracts.operator_contract import validate_operator_semantics
from buyer_ops_contracts.structural import validate_record

ROOT = Path(__file__).resolve().parents[1]


def _valid(name: str) -> dict:
    records = json.loads((ROOT / "tests/fixtures/closure/operator_surface_valid.json").read_text())
    return copy.deepcopy(records[name])


def _ontology(name: str, record_id: str) -> dict:
    records = json.loads((ROOT / "tests/fixtures/generated/ontology_0_3_valid.json").read_text())
    record = copy.deepcopy(records[name])
    record.update(id=record_id, tenantId="value")
    return record


def _mutation_command(command_type: str, target_type: str, target_id: str) -> dict:
    command = _valid("OperatorCommand")
    command.update(
        command_type=command_type,
        target_record_type=target_type,
        target_record_id=target_id,
        expected_version=1,
    )
    command["authority"].update(
        action_class=command_type,
        resource_type=target_type,
        resource_id=target_id,
    )
    return command


def _authorization_command() -> dict:
    command = _mutation_command("revoke_authorization", "Authorization", "authorization-1")
    authorization = _ontology("Authorization", "authorization-1")
    authorization.update(
        version=2,
        authorizationState="revoked",
        grantedAt="2029-01-01T00:00:00Z",
        expiresAt="2031-01-01T00:00:00Z",
        revokedAt="2030-01-01T00:00:00Z",
        revocationEvidenceId="evidence-1",
    )
    command["mutation"] = {
        "kind": "authorization_revocation",
        "authorization_update": authorization,
    }
    return command


def test_operator_policy_requires_one_unambiguous_rule_per_command() -> None:
    policy = _valid("OperatorPolicy")
    validate_operator_semantics(policy)
    policy["command_rules"].append(
        {
            **policy["command_rules"][0],
            "action_class": "different-authority",
        }
    )

    with pytest.raises(ContractViolation) as raised:
        validate_operator_semantics(policy)

    assert {item.code for item in raised.value.violations} == {"DUPLICATE_OPERATOR_COMMAND_RULE"}


def test_operator_command_binds_target_to_authority_and_complete_payload_digest() -> None:
    command = _authorization_command()
    command["payload_digest"] = command_payload_digest(command)
    validate_operator_semantics(command)

    command["authority"]["resource_id"] = "other-approval"
    command["payload_digest"] = command_payload_digest(command)
    with pytest.raises(ContractViolation) as raised:
        validate_operator_semantics(command)
    assert "OPERATOR_AUTHORITY_RESOURCE_MISMATCH" in {item.code for item in raised.value.violations}


def test_operator_command_rejects_digest_that_omits_mutation_payload() -> None:
    command = _authorization_command()
    command["payload_digest"] = command_payload_digest(command)
    command["reason"] = "Changed after digest."

    with pytest.raises(ContractViolation) as raised:
        validate_operator_semantics(command)

    assert "OPERATOR_PAYLOAD_DIGEST_MISMATCH" in {item.code for item in raised.value.violations}


def test_complete_correction_authorization_and_approval_mutations_are_admitted() -> None:
    corrected = _ontology("Assertion", "assertion-1")
    corrected.update(version=2, status="invalid", assertionState="invalid")
    correction = _ontology("Correction", "correction-1")
    correction.update(
        correctedItemId="assertion-1",
        correctionAction="invalidate",
        correctionState="applied",
    )
    correction.pop("replacementItemId", None)
    correction_command = _mutation_command("correct_invalidate", "Assertion", "assertion-1")
    correction_command["mutation"] = {
        "kind": "correction",
        "correction_record": correction,
        "corrected_item_update": corrected,
    }

    authorization = _ontology("Authorization", "authorization-1")
    authorization.update(
        version=2,
        authorizationState="revoked",
        grantedAt="2029-01-01T00:00:00Z",
        expiresAt="2031-01-01T00:00:00Z",
        revokedAt="2030-01-01T00:00:00Z",
        revocationEvidenceId="evidence-1",
    )
    authorization_command = _mutation_command(
        "revoke_authorization", "Authorization", "authorization-1"
    )
    authorization_command["mutation"] = {
        "kind": "authorization_revocation",
        "authorization_update": authorization,
    }

    prior = _ontology("Approval", "approval-1")
    prior.update(
        version=2,
        status="superseded",
        decidedAt="2029-01-01T00:00:00Z",
        expiresAt="2031-01-01T00:00:00Z",
        effectiveTo="2030-01-01T00:00:00Z",
    )
    revoked = _ontology("Approval", "approval-revoked-1")
    revoked.update(
        decision="revoked",
        decidedAt="2030-01-01T00:00:00Z",
        expiresAt="2031-01-01T00:00:00Z",
        effectiveFrom="2030-01-01T00:00:00Z",
        supersedesId="approval-1",
    )
    approval_command = _mutation_command("revoke_approval", "Approval", "approval-1")
    approval_command["mutation"] = {
        "kind": "approval_revocation",
        "prior_approval_update": prior,
        "revoked_approval_record": revoked,
    }

    for command in (correction_command, authorization_command, approval_command):
        command["payload_digest"] = command_payload_digest(command)
        validate_record(command, "operator_surface")
        validate_operator_semantics(command)


@pytest.mark.parametrize("command_type, decision", [("approve", "approved"), ("deny", "denied")])
def test_approval_decision_creates_an_immutable_successor(command_type: str, decision: str) -> None:
    prior = _ontology("Approval", "approval-pending-1")
    prior.update(
        version=2,
        status="superseded",
        decision="pending",
        decidedAt="2029-01-01T00:00:00Z",
        expiresAt="2031-01-01T00:00:00Z",
        effectiveTo="2030-01-01T00:00:00Z",
    )
    successor = _ontology("Approval", f"approval-{decision}-1")
    successor.update(
        version=1,
        status="active",
        decision=decision,
        decidedAt="2030-01-01T00:00:00Z",
        expiresAt="2031-01-01T00:00:00Z",
        effectiveFrom="2030-01-01T00:00:00Z",
        supersedesId=prior["id"],
    )
    command = _mutation_command(command_type, "Approval", prior["id"])
    command["mutation"] = {
        "kind": "approval_decision",
        "prior_approval_update": prior,
        "decided_approval_record": successor,
    }
    command["payload_digest"] = command_payload_digest(command)
    validate_record(command, "operator_surface")
    validate_operator_semantics(command)


def test_workflow_command_binds_reference_successor_and_signal() -> None:
    workflow = _ontology("WorkflowReference", "workflow-reference-1")
    workflow.update(
        tenantId="tenant-1",
        version=2,
        updatedAt="2030-01-01T00:01:00Z",
        subjectId="journey-1",
        executionState="waiting",
        observedAt="2030-01-01T00:01:00Z",
    )
    command = _mutation_command("pause_workflow", "WorkflowReference", workflow["id"])
    command["tenant_id"] = "tenant-1"
    command["journey_id"] = "journey-1"
    command["authority"].update(resource_type="WorkflowReference", resource_id=workflow["id"])
    command["mutation"] = {
        "kind": "workflow_command",
        "workflow_reference_update": workflow,
        "workflow_reference_expected_version": 1,
        "signal_name": "pause",
        "signal_id": "signal-pause-1",
        "signal_payload": {},
    }
    command["payload_digest"] = command_payload_digest(command)
    validate_record(command, "operator_surface")
    validate_operator_semantics(command)

    command["mutation"]["signal_name"] = "resume"
    command["payload_digest"] = command_payload_digest(command)
    with pytest.raises(ContractViolation, match="OPERATOR_WORKFLOW_COMMAND_BINDING"):
        validate_operator_semantics(command)
