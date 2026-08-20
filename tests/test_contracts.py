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
        "connector_gateway",
        "context",
        "gateway",
        "gateway_runtime",
        "habitat",
        "ontology",
        "operator_surface",
        "ot01_ingress",
        "release_activation",
        "telemetry_slo",
        "temporal",
    )
    for name in registry.names:
        Draft202012Validator.check_schema(registry.get(name).schema)


@pytest.mark.parametrize(
    ("fixture", "contract"),
    [
        ("valid/cognitive_work_request.json", "gateway"),
        ("valid/cognitive_proposal.json", "gateway"),
        ("valid/written_buyer_agreement.json", "ontology"),
        ("valid/person.json", "ontology"),
        ("valid/effect_intent.json", "habitat"),
        ("valid/temporal_workflow_input.json", "temporal"),
        ("valid/connector_reconciliation_input.json", "temporal"),
        ("valid/domain_child_input.json", "temporal"),
        ("valid/temporal_worker_configuration.json", "temporal"),
        ("valid/context_compile_request.json", "context"),
        ("valid/gateway_route_policy.json", "gateway_runtime"),
        ("valid/gateway_credential_identity.json", "gateway_runtime"),
        ("valid/gateway_capability_profile.json", "gateway_runtime"),
        ("valid/gateway_failure.json", "gateway_runtime"),
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


def test_gateway_pair_rejects_claim_source_outside_admitted_context(load_fixture) -> None:
    proposal = load_fixture("valid/cognitive_proposal.json")
    proposal["claims"][0]["sourceIds"] = ["cross-buyer-source"]

    with pytest.raises(ContractViolation, match="CLAIM_SOURCE_OUTSIDE_CONTEXT"):
        validate_gateway_pair(
            load_fixture("valid/cognitive_work_request.json"),
            proposal,
            SemanticPolicy(now=datetime(2029, 12, 31, tzinfo=UTC)),
        )


def test_gateway_pair_rejects_context_expired_before_proposal_admission(load_fixture) -> None:
    with pytest.raises(ContractViolation, match="STALE_CONTEXT"):
        validate_gateway_pair(
            load_fixture("valid/cognitive_work_request.json"),
            load_fixture("valid/cognitive_proposal.json"),
            SemanticPolicy(now=datetime(2030, 1, 1, 10, 10, tzinfo=UTC)),
        )


def test_optional_action_not_before_defaults_to_immediate_semantics(load_fixture) -> None:
    proposal = load_fixture("valid/cognitive_proposal.json")
    proposal["proposedActions"][0]["requestedExecutionWindow"].pop("notBefore")

    validate_gateway_pair(
        load_fixture("valid/cognitive_work_request.json"),
        proposal,
        SemanticPolicy(now=datetime(2029, 12, 31, tzinfo=UTC)),
    )


def test_proposal_rejects_claim_freshness_after_generation(load_fixture) -> None:
    proposal = load_fixture("valid/cognitive_proposal.json")
    proposal["claims"][0]["freshnessAt"] = "2030-01-01T10:02:00Z"

    with pytest.raises(ContractViolation, match="CLAIM_FRESHNESS_AFTER_PROPOSAL"):
        validate_gateway_pair(
            load_fixture("valid/cognitive_work_request.json"),
            proposal,
            SemanticPolicy(now=datetime(2029, 12, 31, tzinfo=UTC)),
        )


def test_proposal_rejects_duplicate_recipient_identity(load_fixture) -> None:
    proposal = load_fixture("valid/cognitive_proposal.json")
    proposal["proposedActions"][0]["recipientRefs"] = ["buyer-1", "buyer-1"]

    with pytest.raises(ContractViolation, match="STRUCTURAL_SCHEMA"):
        validate_record(proposal, "gateway")


def test_capability_profile_requires_complete_failure_mapping(load_fixture) -> None:
    profile = load_fixture("valid/gateway_capability_profile.json")
    profile.pop("failureMappings", None)

    with pytest.raises(ContractViolation, match="STRUCTURAL_SCHEMA"):
        validate_record(profile, "gateway_runtime")


def test_gateway_pair_rejects_runtime_evidence_from_other_route_policy(load_fixture) -> None:
    proposal = load_fixture("valid/cognitive_proposal.json")
    proposal["runtimeEvidence"]["routePolicyVersion"] = "route/other"

    with pytest.raises(ContractViolation, match="RUNTIME_ROUTE_POLICY_MISMATCH"):
        validate_gateway_pair(
            load_fixture("valid/cognitive_work_request.json"),
            proposal,
            SemanticPolicy(now=datetime(2029, 12, 31, tzinfo=UTC)),
        )


def test_capability_profile_pins_exactly_one_evaluated_model(load_fixture) -> None:
    profile = load_fixture("valid/gateway_capability_profile.json")
    profile["resolvedModelIds"].append("runtime-discovered-replacement")

    with pytest.raises(ContractViolation, match="STRUCTURAL_SCHEMA"):
        validate_record(profile, "gateway_runtime")


def test_route_policy_can_explicitly_authorize_schema_rejection_transition(load_fixture) -> None:
    policy = load_fixture("valid/gateway_route_policy.json")
    policy["transitions"][0]["allowedCauses"] = ["schema_rejected"]

    validate_record(policy, "gateway_runtime")


def test_confirmed_transaction_milestone_requires_confirmation_evidence(load_fixture) -> None:
    milestone = load_fixture("generated/ontology_0_3_valid.json")["TransactionMilestone"]
    milestone["confirmationState"] = "confirmed"
    milestone.pop("confirmationEvidenceId", None)

    with pytest.raises(ContractViolation, match="MILESTONE_CONFIRMATION_EVIDENCE_REQUIRED"):
        validate_semantics(milestone)


@pytest.mark.parametrize(
    "record",
    [
        {
            "recordType": "Authorization",
            "authorizationState": "active",
            "grantedAt": "2030-01-01T00:00:00Z",
            "expiresAt": "2030-01-02T00:00:00Z",
            "sourceEvidenceIds": ["evidence-1"],
        },
        {
            "recordType": "Approval",
            "decision": "approved",
            "decidedAt": "2030-01-01T00:00:00Z",
            "expiresAt": "2030-01-02T00:00:00Z",
            "sourceEvidenceIds": ["evidence-1"],
        },
        {
            "recordType": "ConnectorGrant",
            "grantState": "active",
            "grantedAt": "2030-01-01T00:00:00Z",
            "expiresAt": "2030-01-02T00:00:00Z",
            "sourceEvidenceIds": ["evidence-1"],
        },
    ],
)
def test_active_authority_and_approval_must_be_unexpired(record: dict[str, object]) -> None:
    with pytest.raises(ContractViolation, match="ACTIVE_AUTHORITY_EXPIRED"):
        validate_semantics(record, SemanticPolicy(now=datetime(2030, 1, 2, tzinfo=UTC)))


def test_agreement_semantics_do_not_invent_required_signer_set(load_fixture) -> None:
    agreement = load_fixture("valid/written_buyer_agreement.json")
    agreement["buyerPartyIds"] = ["party-1", "party-2"]
    agreement["signatureEvidence"] = [
        {
            "signerPartyId": "party-1",
            "signedAt": "2030-01-01T23:00:00Z",
            "evidenceId": "evidence-1",
        }
    ]
    validate_semantics(agreement, SemanticPolicy(now=datetime(2029, 12, 31, tzinfo=UTC)))


def test_proposal_rejects_runtime_evidence_completed_after_generation(load_fixture) -> None:
    proposal = load_fixture("valid/cognitive_proposal.json")
    proposal["runtimeEvidence"]["completedAt"] = "2030-01-01T10:00:31Z"

    with pytest.raises(ContractViolation, match="RUNTIME_EVIDENCE_AFTER_PROPOSAL"):
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


def _open025_digest(character: str) -> str:
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
        "allowedCommands": ["request_reconciliation"],
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
        "buildDigest": _open025_digest("a"),
        "contractManifestDigest": _open025_digest("b"),
        "policyVersion": "policy-1",
        "enabledCapabilities": ["email"],
        "requiredGateIds": ["GATE-002"],
        "gateEvidence": [
            {
                "gateId": "GATE-002",
                "applicability": "platform_invariant",
                "outcome": "blocked",
                "evidenceId": "evidence-1",
                "evidenceDigest": _open025_digest("c"),
                "expiresAt": "2030-02-01T00:00:00Z",
            }
        ],
        "signerActorId": "actor-1",
        "signature": _open025_digest("d"),
        "effectiveAt": "2030-01-01T00:00:00Z",
        "expiresAt": "2030-02-01T00:00:00Z",
        "status": "active",
    }
    validate_record(record, "authority_activation_fair_housing")
    with pytest.raises(ContractViolation, match="NONPASSING_REQUIRED_GATE"):
        validate_authority_activation_fair_housing_semantics(
            record, now=datetime(2030, 1, 2, tzinfo=UTC)
        )
    record["gateEvidence"][0]["outcome"] = "pass"
    record["enabledCapabilities"] = ["email", "outbound_ai_voice"]
    with pytest.raises(ContractViolation, match="PROHIBITED_CAPABILITY"):
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
        "baselineInputDigest": _open025_digest("a"),
        "counterfactualInputDigest": _open025_digest("b"),
        "onlyDifferenceAttestation": True,
        "invariantFields": ["serviceLevel", "cadence"],
        "baselineOutcomeDigest": _open025_digest("c"),
        "counterfactualOutcomeDigest": _open025_digest("d"),
        "outcome": "pass",
    }
    validate_record(record, "authority_activation_fair_housing")
    with pytest.raises(ContractViolation, match="COUNTERFACTUAL_MISMATCH"):
        validate_authority_activation_fair_housing_semantics(record)


def _canonical(record_type: str, **fields: object) -> dict[str, object]:
    return {
        "id": "record-1",
        "tenantId": "tenant-1",
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": record_type,
        "version": 1,
        "createdAt": "2029-01-01T00:00:00Z",
        "updatedAt": "2029-01-01T00:00:00Z",
        "effectiveFrom": "2029-01-01T00:00:00Z",
        "createdBy": {"actorType": "service_principal", "actorId": "service-1"},
        "sourceEvidenceIds": ["evidence-1"],
        "status": "active",
        **fields,
    }


def test_consequential_record_requires_source_evidence() -> None:
    record = _canonical(
        "ConsentGrant",
        personId="person-1",
        channel="email",
        purpose="buyer_consultation",
        principalId="principal-1",
        basis="affirmative",
        grantedAt="2029-01-01T00:00:00Z",
        validityState="active",
    )
    record["sourceEvidenceIds"] = []
    with pytest.raises(ContractViolation, match="SOURCE_EVIDENCE_REQUIRED"):
        validate_semantics(record)


def test_confirmed_appointment_requires_provider_resource_and_version() -> None:
    record = _canonical(
        "Appointment",
        journeyId="journey-1",
        appointmentType="consultation",
        participantIds=["person-1", "agent-1"],
        startsAt="2029-01-02T00:00:00Z",
        endsAt="2029-01-02T01:00:00Z",
        timeZone="America/Chicago",
        appointmentState="confirmed",
    )
    with pytest.raises(ContractViolation, match="CONFIRMED_APPOINTMENT_PROVIDER_REF"):
        validate_semantics(record)


def test_model_output_cannot_directly_create_verified_fact() -> None:
    record = _canonical(
        "VerifiedFact",
        proposition={
            "subjectRef": "person-1",
            "predicate": "preferred_area",
            "value": "Austin",
            "validFrom": "2029-01-01T00:00:00Z",
        },
        verificationRuleId="preferred-area-rule",
        verificationMethod="model:qualification-route",
        supportingEvidenceIds=["evidence-1"],
        verifiedAt="2029-01-01T00:00:00Z",
        factState="current",
    )
    with pytest.raises(ContractViolation, match="MODEL_CANNOT_VERIFY_FACT"):
        validate_semantics(record)


def test_successful_effect_requires_provider_receipt() -> None:
    record = _canonical(
        "EffectAttempt",
        intentId="intent-1",
        actionClass="send_acknowledgment",
        payloadDigest="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        permitDigest="sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        idempotencyKey="effect-1",
        attemptState="confirmed",
    )
    with pytest.raises(ContractViolation, match="PROVIDER_RECEIPT_REQUIRED"):
        validate_semantics(record)
