import copy
from datetime import UTC, datetime, timedelta

import pytest
from conftest import mutate
from jsonschema import Draft202012Validator

from buyer_ops_contracts import (
    ContractRegistry,
    ContractViolation,
    SemanticPolicy,
    validate_authority_activation_fair_housing_semantics,
    validate_gateway_pair,
    validate_record,
    validate_semantics,
)
from buyer_ops_contracts.compatibility import compare_schemas


def test_packaged_schemas_are_valid_and_hash_pinned() -> None:
    registry = ContractRegistry()
    assert registry.names == (
        "authority_activation_fair_housing",
        "closure",
        "gateway",
        "ontology",
    )
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


def _digest(character: str) -> str:
    return f"sha256:{character * 64}"


def test_open_025_authorization_is_versioned_and_current() -> None:
    record = {
        "schemaVersion": "open-025-027/1.0.0",
        "recordType": "ActorTenantAuthorization",
        "tenantId": "tenant-1",
        "recordId": "auth-1",
        "observedAt": "2030-01-01T00:00:00Z",
        "actorId": "actor-1",
        "principalId": "principal-1",
        "role": "agent",
        "allowedCommands": ["journey.update"],
        "recordScopes": ["journey"],
        "policyVersion": "policy-1",
        "authorizationVersion": 1,
        "effectiveAt": "2030-01-01T00:00:00Z",
        "expiresAt": "2030-02-01T00:00:00Z",
        "status": "active",
    }
    validate_record(record, "authority_activation_fair_housing")
    validate_authority_activation_fair_housing_semantics(
        record, now=datetime(2030, 1, 2, tzinfo=UTC)
    )


def test_open_026_rejects_nonpassing_required_gate() -> None:
    record = {
        "schemaVersion": "open-025-027/1.0.0",
        "recordType": "ReleaseActivation",
        "tenantId": "tenant-1",
        "recordId": "activation-1",
        "observedAt": "2030-01-01T00:00:00Z",
        "environment": "production",
        "releaseId": "release-1",
        "buildDigest": _digest("a"),
        "contractManifestDigest": _digest("b"),
        "policyVersion": "policy-1",
        "enabledCapabilities": ["email"],
        "requiredGateIds": ["GATE-002"],
        "gateEvidence": [
            {
                "gateId": "GATE-002",
                "applicability": "platform_invariant",
                "outcome": "blocked",
                "evidenceId": "evidence-1",
                "evidenceDigest": _digest("c"),
                "expiresAt": "2030-02-01T00:00:00Z",
            }
        ],
        "signerActorId": "actor-1",
        "signature": _digest("d"),
        "effectiveAt": "2030-01-01T00:00:00Z",
        "expiresAt": "2030-02-01T00:00:00Z",
        "status": "active",
    }
    validate_record(record, "authority_activation_fair_housing")
    with pytest.raises(ContractViolation, match="NONPASSING_REQUIRED_GATE"):
        validate_authority_activation_fair_housing_semantics(
            record, now=datetime(2030, 1, 2, tzinfo=UTC)
        )


def test_open_027_counterfactual_pass_requires_equal_outcome() -> None:
    record = {
        "schemaVersion": "open-025-027/1.0.0",
        "recordType": "FairHousingCounterfactualCase",
        "tenantId": "tenant-1",
        "recordId": "case-1",
        "observedAt": "2030-01-01T00:00:00Z",
        "profileVersion": "fair-housing-1",
        "caseId": "sussex-token-boundary",
        "actionClass": "lead_qualification",
        "protectedDimension": "sex",
        "baselineInputDigest": _digest("a"),
        "counterfactualInputDigest": _digest("b"),
        "onlyDifferenceAttestation": True,
        "invariantFields": ["serviceLevel", "cadence"],
        "baselineOutcomeDigest": _digest("c"),
        "counterfactualOutcomeDigest": _digest("d"),
        "outcome": "pass",
    }
    validate_record(record, "authority_activation_fair_housing")
    with pytest.raises(ContractViolation, match="COUNTERFACTUAL_MISMATCH"):
        validate_authority_activation_fair_housing_semantics(record)
