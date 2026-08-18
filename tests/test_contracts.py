import copy
from datetime import UTC, datetime, timedelta

import pytest
from conftest import mutate
from jsonschema import Draft202012Validator

from buyer_ops_contracts import (
    ContractRegistry,
    ContractViolation,
    SemanticPolicy,
    validate_gateway_pair,
    validate_record,
    validate_semantics,
)
from buyer_ops_contracts.compatibility import compare_schemas


def test_packaged_schemas_are_valid_and_hash_pinned() -> None:
    registry = ContractRegistry()
    assert registry.names == ("gateway", "ontology")
    for name in registry.names:
        Draft202012Validator.check_schema(registry.get(name).schema)


@pytest.mark.parametrize(
    ("fixture", "contract"),
    [
        ("valid/cognitive_work_request.json", "gateway"),
        ("valid/cognitive_proposal.json", "gateway"),
        ("valid/written_buyer_agreement.json", "ontology"),
    ],
)
def test_valid_fixtures(fixture: str, contract: str, load_fixture) -> None:
    record = load_fixture(fixture)
    validate_record(record, contract)
    policy = SemanticPolicy(
        now=datetime(2029, 12, 31, tzinfo=UTC),
        max_proposal_ttl={"lead_qualification_draft": timedelta(minutes=15)},
    )
    validate_semantics(record, policy)


def test_gateway_pair_is_correlated(load_fixture) -> None:
    policy = SemanticPolicy(now=datetime(2029, 12, 31, tzinfo=UTC))
    validate_gateway_pair(
        load_fixture("valid/cognitive_work_request.json"),
        load_fixture("valid/cognitive_proposal.json"),
        policy,
    )


def test_gateway_pair_rejects_cross_work_proposal(load_fixture) -> None:
    proposal = load_fixture("valid/cognitive_proposal.json")
    proposal["workId"] = "other-work"
    with pytest.raises(ContractViolation, match="REQUEST_PROPOSAL_MISMATCH"):
        validate_gateway_pair(
            load_fixture("valid/cognitive_work_request.json"),
            proposal,
            SemanticPolicy(now=datetime(2029, 12, 31, tzinfo=UTC)),
        )


@pytest.mark.parametrize(
    "fixture",
    ["invalid/proposal_unresolved_claim.json", "invalid/agreement_term_exceeded.json"],
)
def test_semantic_negative_fixtures(fixture: str, load_fixture) -> None:
    spec = load_fixture(fixture)
    record = mutate(
        load_fixture(spec["fixtureBase"]), spec["mutation"]["path"], spec["mutation"]["value"]
    )
    with pytest.raises(ContractViolation) as raised:
        validate_semantics(record, SemanticPolicy(now=datetime(2029, 12, 31, tzinfo=UTC)))
    assert spec["expectedCode"] in {item.code for item in raised.value.violations}


def test_structural_validator_rejects_unknown_fields(load_fixture) -> None:
    record = load_fixture("valid/cognitive_work_request.json")
    record["modelMayWrite"] = True
    with pytest.raises(ContractViolation, match="STRUCTURAL_SCHEMA"):
        validate_record(record, "gateway")


def test_approval_disposition_fails_closed(load_fixture) -> None:
    record = load_fixture("valid/cognitive_proposal.json")
    record["policyDisposition"] = "prohibited"
    record["requiredApproval"] = "agent"
    with pytest.raises(ContractViolation, match="STRUCTURAL_SCHEMA"):
        validate_record(record, "gateway")


def test_compatibility_detects_required_field_addition() -> None:
    previous = {"type": "object", "properties": {"id": {"type": "string"}}}
    current = copy.deepcopy(previous)
    current["required"] = ["id"]
    findings = compare_schemas(previous, current)
    assert [(item.rule, item.breaking) for item in findings] == [("REQUIRED_ADDED", True)]


def test_compatibility_allows_optional_property_addition() -> None:
    previous = {"type": "object", "properties": {"id": {"type": "string"}}}
    current = copy.deepcopy(previous)
    current["properties"]["note"] = {"type": "string"}
    assert compare_schemas(previous, current) == []

