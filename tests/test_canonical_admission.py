import json
from copy import deepcopy
from pathlib import Path

import pytest

from buyer_ops_contracts.canonical_admission import (
    _DECLARED_TRANSITIONS,
    _STATE_FIELDS,
    validate_agreement_qualification,
    validate_reference_graph,
    validate_representation_relationship,
    validate_update,
    validate_verified_fact_admission,
)
from buyer_ops_contracts.errors import ContractViolation


def test_lifecycle_registry_covers_every_schema_state_field_and_value() -> None:
    schema = json.loads((Path(__file__).parents[1] / "ONTOLOGY-V0.schema.json").read_text())
    expected_keys = {
        (record_type, field) for record_type, fields in _STATE_FIELDS.items() for field in fields
    }
    assert set(_DECLARED_TRANSITIONS) == expected_keys
    for record_type, field in expected_keys:
        definition = schema["$defs"][record_type]
        property_schema = next(
            part["properties"][field]
            for part in definition["allOf"]
            if field in part.get("properties", {})
        )
        graph = _DECLARED_TRANSITIONS[(record_type, field)]
        represented = set(graph)
        represented.update(target for targets in graph.values() for target in targets)
        assert represented == set(property_schema["enum"]), (record_type, field)


def _epistemic(*, version: int, validity: str = "current"):
    return {
        "id": "item-1",
        "tenantId": "tenant-1",
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "Assertion",
        "version": version,
        "createdAt": "2029-01-01T00:00:00Z",
        "updatedAt": f"2029-01-0{version}T00:00:00Z",
        "effectiveFrom": "2029-01-01T00:00:00Z",
        "createdBy": {"actorType": "person", "actorId": "person-1"},
        "sourceEvidenceIds": ["evidence-1"],
        "status": "active",
        "proposition": {
            "subjectRef": "person-1",
            "predicate": "timeline",
            "value": "six_months",
            "validFrom": "2029-01-01T00:00:00Z",
        },
        "speakerType": "person",
        "speakerId": "person-1",
        "sourceLocation": "message:1#span:1",
        "assertionState": validity,
    }


def test_update_rejects_identity_mutation_and_epistemic_reactivation() -> None:
    previous = _epistemic(version=1, validity="contradicted")
    current = _epistemic(version=2)
    current["createdBy"] = {"actorType": "person", "actorId": "other-person"}
    with pytest.raises(ContractViolation) as raised:
        validate_update(previous, current)
    assert {item.code for item in raised.value.violations} == {
        "IMMUTABLE_CANONICAL_FIELD",
        "INVALID_STATE_TRANSITION",
    }


def test_epistemic_correction_requires_replacement_item() -> None:
    previous = _epistemic(version=1)
    current = deepcopy(_epistemic(version=2))
    current["proposition"]["value"] = "twelve_months"
    with pytest.raises(ContractViolation, match="EPISTEMIC_IDENTITY_MUTATION"):
        validate_update(previous, current)


def test_agreement_cannot_skip_declared_execution_states() -> None:
    previous = _epistemic(version=1)
    previous.update(
        recordType="WrittenBuyerAgreement",
        executionState="draft",
    )
    previous.pop("assertionState")
    previous.pop("speakerType")
    previous.pop("speakerId")
    previous.pop("sourceLocation")
    previous.pop("proposition")
    current = deepcopy(previous)
    current["version"] = 2
    current["updatedAt"] = "2029-01-02T00:00:00Z"
    current["executionState"] = "effective"
    with pytest.raises(ContractViolation, match="INVALID_STATE_TRANSITION"):
        validate_update(previous, current)


def test_approval_payload_binding_is_immutable() -> None:
    previous = _epistemic(version=1)
    previous.update(
        recordType="Approval",
        approverType="license_holder",
        approverId="agent-1",
        actionClass="send_message",
        actionIntentId="intent-1",
        payloadDigest="sha256:" + "a" * 64,
        scope="recipient:person-1",
        decision="approved",
    )
    for field in ("assertionState", "speakerType", "speakerId", "sourceLocation", "proposition"):
        previous.pop(field)
    current = deepcopy(previous)
    current["version"] = 2
    current["updatedAt"] = "2029-01-02T00:00:00Z"
    current["payloadDigest"] = "sha256:" + "b" * 64
    with pytest.raises(ContractViolation, match="IMMUTABLE_APPROVAL_BINDING"):
        validate_update(previous, current)


def test_connector_grant_binds_typed_principal_grantor_and_revocation_evidence() -> None:
    record = {
        "recordType": "ConnectorGrant",
        "tenantId": "tenant-1",
        "createdBy": {"actorType": "system_migration", "actorId": "migration-1"},
        "sourceEvidenceIds": [],
        "delegatedPrincipalType": "service_principal",
        "delegatedPrincipalId": "principal-1",
        "grantorType": "license_holder",
        "grantorId": "agent-1",
        "grantState": "revoked",
        "revocationEvidenceId": "revocation-1",
    }
    records = {
        "principal-1": {"tenantId": "tenant-1", "recordType": "Person"},
        "agent-1": {"tenantId": "tenant-1", "recordType": "LicenseHolder"},
        "revocation-1": {"tenantId": "tenant-1", "recordType": "Evidence"},
    }
    with pytest.raises(ContractViolation, match="REFERENCE_TYPE_MISMATCH"):
        validate_reference_graph(record, records.get)


def test_transaction_confirmed_date_binding_requires_owner_and_confirmed_state() -> None:
    record = {
        "id": "transaction-1",
        "recordType": "Transaction",
        "tenantId": "tenant-1",
        "createdBy": {"actorType": "system_migration", "actorId": "migration-1"},
        "sourceEvidenceIds": [],
        "journeyId": "journey-1",
        "buyingPartyId": "party-1",
        "brokerageId": "broker-1",
        "propertyReferenceId": "property-1",
        "executedArtifactId": "artifact-1",
        "executedArtifactDigest": "sha256:" + "a" * 64,
        "confirmedDateIds": ["date-1"],
        "partyIds": [],
    }
    records = {
        "journey-1": {"tenantId": "tenant-1", "recordType": "BuyerJourney"},
        "party-1": {"tenantId": "tenant-1", "recordType": "BuyingParty"},
        "broker-1": {"tenantId": "tenant-1", "recordType": "Brokerage"},
        "property-1": {"tenantId": "tenant-1", "recordType": "PropertyReference"},
        "artifact-1": {
            "tenantId": "tenant-1",
            "recordType": "DocumentArtifact",
            "digest": "sha256:" + "a" * 64,
        },
        "date-1": {
            "tenantId": "tenant-1",
            "recordType": "ConfirmedTransactionDate",
            "transactionId": "transaction-other",
            "confirmationState": "proposed",
        },
    }
    with pytest.raises(ContractViolation) as raised:
        validate_reference_graph(record, records.get)
    assert {item.code for item in raised.value.violations} == {
        "TRANSACTION_DATE_OWNER_MISMATCH",
        "TRANSACTION_DATE_NOT_CONFIRMED",
    }


def test_typed_reference_graph_rejects_missing_wrong_type_and_cross_tenant() -> None:
    record = {
        "recordType": "BuyerJourney",
        "tenantId": "tenant-1",
        "buyingPartyId": "party-1",
        "ownerLicenseHolderId": "agent-1",
        "leadSourceId": "source-1",
    }
    targets = {
        "party-1": {"recordType": "Person", "tenantId": "tenant-1"},
        "agent-1": {"recordType": "LicenseHolder", "tenantId": "tenant-2"},
    }
    with pytest.raises(ContractViolation) as raised:
        validate_reference_graph(record, targets.get)
    assert {item.code for item in raised.value.violations} == {
        "REFERENCE_TYPE_MISMATCH",
        "CROSS_TENANT_REFERENCE",
        "REFERENCE_NOT_FOUND",
    }


def test_typed_reference_graph_accepts_declared_same_tenant_targets() -> None:
    record = {
        "recordType": "BuyingParty",
        "tenantId": "tenant-1",
        "members": [{"personId": "person-1", "role": "buyer"}],
    }
    targets = {"person-1": {"recordType": "Person", "tenantId": "tenant-1"}}
    validate_reference_graph(record, targets.get)


def test_buying_party_member_must_resolve_to_same_tenant_person() -> None:
    record = {
        "recordType": "BuyingParty",
        "tenantId": "tenant-1",
        "members": [{"personId": "not-a-person", "role": "buyer"}],
    }
    targets = {"not-a-person": {"recordType": "ServicePrincipal", "tenantId": "tenant-1"}}

    with pytest.raises(ContractViolation) as raised:
        validate_reference_graph(record, targets.get)

    assert [(item.code, item.path) for item in raised.value.violations] == [
        ("REFERENCE_TYPE_MISMATCH", "$.members.0.personId")
    ]


def test_authorization_actor_discriminators_bind_canonical_reference_types() -> None:
    record = {
        "recordType": "Authorization",
        "tenantId": "tenant-1",
        "grantorType": "brokerage",
        "grantorId": "brokerage-1",
        "granteeType": "service_principal",
        "granteeId": "not-a-service-principal",
        "resourceType": "BuyerJourney",
        "resourceId": "journey-1",
    }
    targets = {
        "brokerage-1": {"recordType": "Brokerage", "tenantId": "tenant-1"},
        "not-a-service-principal": {"recordType": "Person", "tenantId": "tenant-1"},
    }

    with pytest.raises(ContractViolation) as raised:
        validate_reference_graph(record, targets.get)

    assert [(item.code, item.path) for item in raised.value.violations] == [
        ("REFERENCE_TYPE_MISMATCH", "$.granteeId")
    ]


def test_epistemic_proposition_applicable_journey_is_typed() -> None:
    record = {
        "recordType": "VerifiedFact",
        "tenantId": "tenant-1",
        "supportingEvidenceIds": [],
        "proposition": {
            "subjectRef": "person-1",
            "predicate": "timeline",
            "value": "six_months",
            "applicableJourneyId": "not-a-journey",
            "validFrom": "2029-01-01T00:00:00Z",
        },
    }
    targets = {"not-a-journey": {"recordType": "Person", "tenantId": "tenant-1"}}

    with pytest.raises(ContractViolation) as raised:
        validate_reference_graph(record, targets.get)

    assert [(item.code, item.path) for item in raised.value.violations] == [
        ("REFERENCE_TYPE_MISMATCH", "$.proposition.applicableJourneyId")
    ]


def test_memory_scope_discriminator_binds_scope_reference_type() -> None:
    record = {
        "recordType": "Memory",
        "tenantId": "tenant-1",
        "scopeType": "conversation",
        "scopeId": "not-a-conversation",
        "sourceItemIds": [],
    }
    targets = {"not-a-conversation": {"recordType": "BuyerJourney", "tenantId": "tenant-1"}}

    with pytest.raises(ContractViolation) as raised:
        validate_reference_graph(record, targets.get)

    assert [(item.code, item.path) for item in raised.value.violations] == [
        ("REFERENCE_TYPE_MISMATCH", "$.scopeId")
    ]


def test_agreement_signature_party_must_be_an_agreement_party_type() -> None:
    record = {
        "recordType": "WrittenBuyerAgreement",
        "tenantId": "tenant-1",
        "brokerPartyId": "brokerage-1",
        "responsibleLicenseHolderId": "agent-1",
        "buyerPartyIds": ["party-1"],
        "executedArtifactId": "artifact-1",
        "executedArtifactDigest": "sha256:" + "a" * 64,
        "signatureEvidence": [{"signerPartyId": "person-1", "evidenceId": "evidence-1"}],
    }
    targets = {
        "brokerage-1": {"recordType": "Brokerage", "tenantId": "tenant-1"},
        "agent-1": {"recordType": "LicenseHolder", "tenantId": "tenant-1"},
        "party-1": {"recordType": "BuyingParty", "tenantId": "tenant-1"},
        "artifact-1": {
            "recordType": "DocumentArtifact",
            "tenantId": "tenant-1",
            "digest": "sha256:" + "a" * 64,
        },
        "person-1": {"recordType": "Person", "tenantId": "tenant-1"},
        "evidence-1": {"recordType": "Evidence", "tenantId": "tenant-1"},
    }

    with pytest.raises(ContractViolation) as raised:
        validate_reference_graph(record, targets.get)

    assert [(item.code, item.path) for item in raised.value.violations] == [
        ("REFERENCE_TYPE_MISMATCH", "$.signatureEvidence.0.signerPartyId")
    ]


@pytest.mark.parametrize(
    ("record", "path"),
    [
        (
            {"recordType": "LeadSource", "tenantId": "tenant-1", "evidenceId": "wrong"},
            "$.evidenceId",
        ),
        (
            {
                "recordType": "Authorization",
                "tenantId": "tenant-1",
                "grantorType": "person",
                "grantorId": "grantor",
                "granteeType": "person",
                "granteeId": "grantee",
                "revocationEvidenceId": "wrong",
            },
            "$.revocationEvidenceId",
        ),
    ],
)
def test_declared_singular_evidence_links_require_evidence(record: dict, path: str) -> None:
    targets = {
        "grantor": {"recordType": "Person", "tenantId": "tenant-1"},
        "grantee": {"recordType": "Person", "tenantId": "tenant-1"},
        "wrong": {"recordType": "Assertion", "tenantId": "tenant-1"},
    }

    with pytest.raises(ContractViolation) as raised:
        validate_reference_graph(record, targets.get)

    assert ("REFERENCE_TYPE_MISMATCH", path) in [
        (item.code, item.path) for item in raised.value.violations
    ]


def test_canonical_creator_actor_reference_follows_actor_discriminator() -> None:
    record = {
        "recordType": "LeadSource",
        "tenantId": "tenant-1",
        "createdBy": {"actorType": "license_holder", "actorId": "not-an-agent"},
    }
    targets = {"not-an-agent": {"recordType": "Person", "tenantId": "tenant-1"}}

    with pytest.raises(ContractViolation) as raised:
        validate_reference_graph(record, targets.get)

    assert [(item.code, item.path) for item in raised.value.violations] == [
        ("REFERENCE_TYPE_MISMATCH", "$.createdBy.actorId")
    ]


def test_correction_attribution_actor_reference_follows_actor_discriminator() -> None:
    record = {
        "recordType": "Correction",
        "tenantId": "tenant-1",
        "correctedItemId": "item-1",
        "correctionEvidenceIds": ["evidence-1"],
        "attributedTo": {"actorType": "service_principal", "actorId": "not-a-principal"},
    }
    targets = {
        "item-1": {"recordType": "Assertion", "tenantId": "tenant-1"},
        "evidence-1": {"recordType": "Evidence", "tenantId": "tenant-1"},
        "not-a-principal": {"recordType": "Person", "tenantId": "tenant-1"},
    }

    with pytest.raises(ContractViolation) as raised:
        validate_reference_graph(record, targets.get)

    assert [(item.code, item.path) for item in raised.value.violations] == [
        ("REFERENCE_TYPE_MISMATCH", "$.attributedTo.actorId")
    ]


def test_assertion_internal_speaker_reference_follows_speaker_discriminator() -> None:
    record = {
        "recordType": "Assertion",
        "tenantId": "tenant-1",
        "speakerType": "service_principal",
        "speakerId": "not-a-principal",
        "sourceEvidenceIds": [],
        "proposition": {
            "subjectRef": "person-1",
            "predicate": "timeline",
            "value": "six_months",
            "validFrom": "2029-01-01T00:00:00Z",
        },
    }
    targets = {"not-a-principal": {"recordType": "Person", "tenantId": "tenant-1"}}

    with pytest.raises(ContractViolation) as raised:
        validate_reference_graph(record, targets.get)

    assert [(item.code, item.path) for item in raised.value.violations] == [
        ("REFERENCE_TYPE_MISMATCH", "$.speakerId")
    ]


def test_approval_named_actor_reference_follows_approver_discriminator() -> None:
    record = {
        "recordType": "Approval",
        "tenantId": "tenant-1",
        "approverType": "license_holder",
        "approverId": "not-an-agent",
    }
    targets = {"not-an-agent": {"recordType": "Person", "tenantId": "tenant-1"}}

    with pytest.raises(ContractViolation) as raised:
        validate_reference_graph(record, targets.get)

    assert [(item.code, item.path) for item in raised.value.violations] == [
        ("REFERENCE_TYPE_MISMATCH", "$.approverId")
    ]


def test_contradiction_named_scope_reference_follows_scope_discriminator() -> None:
    record = {
        "recordType": "Contradiction",
        "tenantId": "tenant-1",
        "leftItemId": "left",
        "rightItemId": "right",
        "scopeType": "agreement",
        "scopeId": "not-an-agreement",
    }
    targets = {
        "left": {"recordType": "Assertion", "tenantId": "tenant-1"},
        "right": {"recordType": "VerifiedFact", "tenantId": "tenant-1"},
        "not-an-agreement": {"recordType": "Person", "tenantId": "tenant-1"},
    }

    with pytest.raises(ContractViolation) as raised:
        validate_reference_graph(record, targets.get)

    assert [(item.code, item.path) for item in raised.value.violations] == [
        ("REFERENCE_TYPE_MISMATCH", "$.scopeId")
    ]


@pytest.mark.parametrize(
    ("source_type", "target_type"),
    [("message", "Person"), ("document_artifact", "Message")],
)
def test_evidence_canonical_source_reference_follows_source_discriminator(
    source_type: str, target_type: str
) -> None:
    record = {
        "recordType": "Evidence",
        "tenantId": "tenant-1",
        "sourceType": source_type,
        "sourceRef": "wrong-source",
    }
    targets = {"wrong-source": {"recordType": target_type, "tenantId": "tenant-1"}}

    with pytest.raises(ContractViolation) as raised:
        validate_reference_graph(record, targets.get)

    assert [(item.code, item.path) for item in raised.value.violations] == [
        ("REFERENCE_TYPE_MISMATCH", "$.sourceRef")
    ]


class _Verifier:
    def __init__(self, result: bool) -> None:
        self.result = result

    def verify(self, rule_id, proposition, evidence) -> bool:
        return self.result and rule_id == "email-provider-confirmation-v1" and bool(evidence)


def test_verified_fact_requires_governed_predicate_rule_and_exact_evidence() -> None:
    fact = {
        "verificationRuleId": "email-provider-confirmation-v1",
        "proposition": {"subjectRef": "person-1", "predicate": "email_verified", "value": True},
        "supportingEvidenceIds": ["evidence-1"],
    }
    evidence = [{"id": "evidence-1", "recordType": "Evidence"}]
    with pytest.raises(ContractViolation, match="VERIFICATION_RULE_UNAVAILABLE"):
        validate_verified_fact_admission(fact, evidence, None)
    with pytest.raises(ContractViolation, match="VERIFICATION_EVIDENCE_MISMATCH"):
        validate_verified_fact_admission(fact, [], _Verifier(True))
    with pytest.raises(ContractViolation, match="FACT_VERIFICATION_FAILED"):
        validate_verified_fact_admission(fact, evidence, _Verifier(False))
    validate_verified_fact_admission(fact, evidence, _Verifier(True))


def test_showing_only_agreement_cannot_create_representation() -> None:
    relationship = {
        "recordType": "RepresentationRelationship",
        "relationshipState": "active",
        "agreementId": "agreement-1",
        "brokerageId": "broker-1",
        "buyingPartyId": "party-1",
        "effectiveFrom": "2029-01-02T00:00:00Z",
    }
    agreement = {
        "recordType": "WrittenBuyerAgreement",
        "agreementType": "non_representation_showing",
        "executionState": "effective",
        "brokerPartyId": "broker-1",
        "buyerPartyIds": ["party-1"],
        "effectiveAt": "2029-01-01T00:00:00Z",
        "terminatesAt": "2029-01-15T00:00:00Z",
    }
    with pytest.raises(ContractViolation, match="NON_REPRESENTATION_CANNOT_REPRESENT"):
        validate_representation_relationship(relationship, agreement)


def test_showing_only_agreement_cannot_qualify_offer_presentation() -> None:
    agreement = {
        "id": "agreement-1",
        "version": 3,
        "recordType": "WrittenBuyerAgreement",
        "agreementType": "non_representation_showing",
        "executionState": "effective",
        "brokerPartyId": "broker-1",
        "responsibleLicenseHolderId": "agent-1",
        "buyerPartyIds": ["party-1"],
        "effectiveAt": "2029-01-01T00:00:00Z",
        "terminatesAt": "2029-01-15T00:00:00Z",
        "serviceDefinitions": [{"serviceCode": "showing_access", "allowed": True}],
    }
    qualification = {
        "result": "qualified",
        "agreementId": "agreement-1",
        "agreementVersion": 3,
        "actionType": "residential_offer_presentation",
        "brokerageId": "broker-1",
        "responsibleLicenseHolderId": "agent-1",
        "buyerPartyId": "party-1",
        "evaluatedAt": "2029-01-02T00:00:00Z",
    }
    with pytest.raises(ContractViolation, match="OFFER_NOT_COVERED_BY_AGREEMENT"):
        validate_agreement_qualification(qualification, agreement)
