"""Cross-version and cross-record admission rules for canonical writes."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from .errors import ContractViolation, Violation


class FactVerifier(Protocol):
    """Deployment-owned predicate verification rules; model opinion is never sufficient."""

    def verify(
        self,
        rule_id: str,
        proposition: dict[str, Any],
        evidence: Sequence[dict[str, Any]],
    ) -> bool: ...


_STATE_FIELDS: dict[str, tuple[str, ...]] = {
    "Tenant": ("tenantState",),
    "Brokerage": ("brokerageState",),
    "LicenseHolder": ("licenseState",),
    "ServicePrincipal": ("principalState",),
    "Person": ("identityState",),
    "ContactEndpoint": ("ownershipState", "verificationState", "contactabilityState"),
    "BuyingParty": ("decisionAuthorityState",),
    "BuyerJourney": ("journeyState", "qualificationState", "representationState"),
    "Conversation": ("conversationState",),
    "Message": ("deliveryState",),
    "ConsentGrant": ("validityState",),
    "Suppression": ("validityState",),
    "LeadSource": ("attributionState",),
    "QualificationCriterion": ("criterionState",),
    "QualificationObservation": ("observationState",),
    "BuyerRequirement": ("requirementState",),
    "FinancingReadiness": ("readinessState",),
    "Appointment": ("appointmentState",),
    "Commitment": ("commitmentState",),
    "DocumentArtifact": ("artifactState",),
    "IabsDelivery": ("validityState",),
    "WrittenBuyerAgreement": ("executionState",),
    "AgreementQualification": ("result",),
    "RepresentationRelationship": ("relationshipState",),
    "Transaction": ("transactionState",),
    "TransactionMilestone": ("confirmationState", "milestoneState"),
    "Authorization": ("authorizationState",),
    "Approval": ("decision",),
    "EffectAttempt": ("attemptState",),
    "Evidence": ("evidenceState",),
    "Assertion": ("assertionState",),
    "VerifiedFact": ("factState",),
    "Inference": ("inferenceState",),
    "Memory": ("memoryState",),
    "Contradiction": ("resolutionState",),
    "Correction": ("correctionState",),
    "WorkflowReference": ("executionState",),
    "ConnectorGrant": ("grantState",),
    "ConfirmedTransactionDate": ("confirmationState",),
}

_UNIVERSAL_STATUS_TRANSITIONS = {
    "active": {"inactive", "superseded", "tombstoned", "invalid"},
    "inactive": {"active", "superseded", "tombstoned"},
    "invalid": {"superseded", "tombstoned"},
    "superseded": set(),
    "tombstoned": set(),
}

_DECLARED_TRANSITIONS: dict[tuple[str, str], dict[str, set[str]]] = {
    ("Tenant", "tenantState"): {
        "provisioning": {"active"},
        "active": {"suspended", "decommissioning"},
        "suspended": {"active", "decommissioning"},
        "decommissioning": {"decommissioned"},
    },
    ("Brokerage", "brokerageState"): {
        "pending_verification": {"active"},
        "active": {"suspended", "inactive"},
        "suspended": {"active", "inactive"},
    },
    ("LicenseHolder", "licenseState"): {
        "pending_verification": {"active"},
        "active": {"inactive", "suspended", "expired", "revoked"},
        "suspended": {"active", "inactive", "revoked"},
    },
    ("ServicePrincipal", "principalState"): {
        "provisioning": {"active"},
        "active": {"suspended", "revoked"},
        "suspended": {"active", "revoked"},
    },
    ("Person", "identityState"): {
        "provisional": {"resolved", "ambiguous", "conflict"},
        "ambiguous": {"resolved"},
        "conflict": {"resolved"},
    },
    ("ContactEndpoint", "ownershipState"): {
        "asserted": {"authorized", "disputed", "revoked"},
    },
    ("ContactEndpoint", "verificationState"): {
        "unverified": {"provider_observed"},
        "provider_observed": {"verified", "disputed"},
    },
    ("ContactEndpoint", "contactabilityState"): {
        "unknown": {"contactable", "temporarily_unavailable", "suppressed", "invalid"},
        "contactable": {"temporarily_unavailable", "suppressed", "invalid"},
        "temporarily_unavailable": {"contactable", "suppressed", "invalid"},
    },
    ("BuyingParty", "decisionAuthorityState"): {
        "unconfirmed": {"individual", "joint", "delegated", "disputed"},
        "disputed": {"individual", "joint", "delegated"},
    },
    ("BuyerJourney", "qualificationState"): {
        "not_started": {"collecting"},
        "collecting": {"sufficient_for_consult", "stale", "contradicted"},
        "sufficient_for_consult": {"stale", "contradicted"},
        "declined": set(),
    },
    ("BuyerJourney", "representationState"): {
        "unconfirmed": {"not_represented", "agreement_pending", "conflict"},
        "not_represented": {"agreement_pending", "conflict"},
        "agreement_pending": {
            "represented",
            "non_representation_showing_only",
            "expired",
            "terminated",
            "conflict",
        },
        "represented": {"expired", "terminated", "conflict"},
        "non_representation_showing_only": {"expired", "terminated", "conflict"},
        "expired": {"agreement_pending"},
        "terminated": {"agreement_pending"},
        "conflict": {"not_represented", "agreement_pending", "represented"},
    },
    ("BuyerJourney", "journeyState"): {
        "captured": {"contacted", "suppressed", "ineligible", "released", "dormant", "blocked"},
        "contacted": {"qualifying", "suppressed", "ineligible", "released", "dormant", "blocked"},
        "qualifying": {
            "nurture",
            "consultation_ready",
            "suppressed",
            "ineligible",
            "released",
            "dormant",
            "blocked",
        },
        "nurture": {
            "qualifying",
            "consultation_ready",
            "suppressed",
            "released",
            "dormant",
            "blocked",
        },
        "consultation_ready": {
            "consultation_booked",
            "nurture",
            "suppressed",
            "released",
            "dormant",
            "blocked",
        },
        "consultation_booked": {"representation_pending", "suppressed", "released", "blocked"},
        "representation_pending": {"represented", "searching", "suppressed", "released", "blocked"},
        "represented": {"searching", "released", "blocked"},
        "searching": {"under_contract", "released", "dormant", "blocked"},
        "under_contract": {"closed", "released", "blocked"},
        "dormant": {"contacted", "qualifying"},
        "blocked": {"contacted", "qualifying", "representation_pending", "searching"},
    },
    ("Conversation", "conversationState"): {
        "open": {"closed", "blocked"},
        "blocked": {"open", "closed"},
        "closed": {"archived"},
    },
    ("Message", "deliveryState"): {
        "observed": set(),
        "queued": {"sent", "failed", "unknown_outcome", "suppressed"},
        "sent": {"delivered", "failed", "unknown_outcome"},
        "unknown_outcome": {"delivered", "failed"},
    },
    ("ConsentGrant", "validityState"): {
        "active": {"expired", "revoked", "superseded", "disputed"},
        "disputed": {"active", "revoked"},
    },
    ("Suppression", "validityState"): {"active": {"lifted", "superseded"}},
    ("LeadSource", "attributionState"): {
        "observed": {"confirmed", "disputed", "superseded"},
        "confirmed": {"superseded"},
        "disputed": {"superseded"},
    },
    ("QualificationCriterion", "criterionState"): {
        "draft": {"active"},
        "active": {"retired", "superseded"},
    },
    ("QualificationObservation", "observationState"): {
        "unknown": {"buyer_declined", "asserted", "inferred", "not_applicable"},
        "buyer_declined": {"asserted", "inferred", "not_applicable"},
        "asserted": {"verified", "inferred", "stale", "contradicted"},
        "verified": {"stale", "contradicted"},
        "inferred": {"verified", "stale", "contradicted"},
    },
    ("BuyerRequirement", "requirementState"): {
        "asserted": {"confirmed", "stale", "contradicted", "withdrawn", "superseded"},
        "confirmed": {"stale", "contradicted", "withdrawn", "superseded"},
    },
    ("FinancingReadiness", "readinessState"): {
        "unknown": {"buyer_reported", "documentation_pending", "not_applicable"},
        "buyer_reported": {"documentation_pending", "documented", "stale", "contradicted"},
        "documentation_pending": {"documented", "stale", "contradicted"},
        "documented": {"stale", "contradicted"},
    },
    ("Commitment", "commitmentState"): {
        "open": {"in_progress", "blocked", "fulfilled", "failed", "cancelled", "superseded"},
        "in_progress": {"blocked", "fulfilled", "failed", "cancelled", "superseded"},
        "blocked": {"in_progress", "fulfilled", "failed", "cancelled", "superseded"},
    },
    ("DocumentArtifact", "artifactState"): {
        "pending": {"active", "invalid", "deleted"},
        "active": {"superseded", "deleted", "anonymized", "invalid"},
    },
    ("IabsDelivery", "validityState"): {
        "delivery_unknown": {"delivered", "invalid"},
        "delivered": {"superseded", "invalid"},
    },
    ("WrittenBuyerAgreement", "executionState"): {
        "draft": {"agent_approved", "void"},
        "agent_approved": {"presented", "void"},
        "presented": {"partially_signed", "executed", "void"},
        "partially_signed": {"executed", "void"},
        "executed": {"effective", "void"},
        "effective": {"expired", "terminated", "superseded", "void"},
    },
    ("AgreementQualification", "result"): {
        "qualified": set(),
        "denied": set(),
        "requires_resolution": set(),
    },
    ("RepresentationRelationship", "relationshipState"): {
        "active": {"expired", "terminated", "conflict", "superseded"},
        "conflict": {"active", "terminated", "superseded"},
    },
    ("Transaction", "transactionState"): {
        "under_contract": {"active", "terminated", "cancelled", "disputed"},
        "active": {"closing_pending", "terminated", "cancelled", "disputed"},
        "closing_pending": {"closed", "terminated", "disputed"},
    },
    ("TransactionMilestone", "confirmationState"): {
        "proposed": {"confirmed", "disputed"},
    },
    ("TransactionMilestone", "milestoneState"): {
        "pending": {"due", "completed", "waived", "cancelled", "superseded"},
        "due": {"completed", "missed", "waived", "cancelled", "superseded"},
    },
    ("Authorization", "authorizationState"): {
        "pending": {"active", "revoked"},
        "active": {"expired", "revoked", "superseded", "disputed"},
        "disputed": {"active", "revoked"},
    },
    ("Approval", "decision"): {
        "pending": {"approved", "denied", "revoked"},
        "approved": set(),
        "denied": set(),
        "revoked": set(),
    },
    ("Appointment", "appointmentState"): {
        "proposed": {"held", "cancelled"},
        "held": {"provider_pending", "cancelled", "no_show"},
        "provider_pending": {"confirmed", "cancelled", "unknown_outcome"},
        "confirmed": {"completed", "cancelled", "no_show", "unknown_outcome"},
        "unknown_outcome": {"confirmed", "cancelled", "no_show"},
    },
    ("EffectAttempt", "attemptState"): {
        "registered": {"dispatching"},
        "dispatching": {"accepted", "confirmed", "rejected", "unknown_outcome"},
        "accepted": {"confirmed", "rejected", "unknown_outcome"},
        "unknown_outcome": {"reconciled_failed", "reconciled_succeeded"},
    },
    ("Evidence", "evidenceState"): {"current": {"superseded", "deleted", "anonymized", "invalid"}},
    ("Assertion", "assertionState"): {
        "current": {"stale", "contradicted", "superseded", "withdrawn", "invalid"},
    },
    ("VerifiedFact", "factState"): {
        "current": {"stale", "contradicted", "superseded", "revoked", "invalid"},
    },
    ("Inference", "inferenceState"): {
        "current": {"stale", "contradicted", "superseded", "invalid"},
    },
    ("Memory", "memoryState"): {
        "current": {"stale", "invalidated", "superseded", "invalid"},
    },
    ("Contradiction", "resolutionState"): {
        "open": {
            "under_review",
            "resolved_left",
            "resolved_right",
            "resolved_replacement",
            "dismissed",
            "superseded",
        },
        "under_review": {
            "resolved_left",
            "resolved_right",
            "resolved_replacement",
            "dismissed",
            "superseded",
        },
    },
    ("Correction", "correctionState"): {
        "proposed": {"applied", "rejected", "superseded"},
    },
    ("WorkflowReference", "executionState"): {
        "created": {"running", "waiting", "cancelled", "terminated"},
        "running": {"waiting", "completed", "failed", "cancelled", "terminated", "unknown"},
        "waiting": {"running", "completed", "failed", "cancelled", "terminated", "unknown"},
        "unknown": {"running", "waiting", "completed", "failed", "terminated"},
    },
    ("ConnectorGrant", "grantState"): {
        "pending": {"active"},
        "active": {"suspended", "revoked", "expired", "superseded"},
        "suspended": {"active", "revoked", "expired", "superseded"},
    },
    ("ConfirmedTransactionDate", "confirmationState"): {
        "proposed": {"confirmed", "invalidated"},
        "confirmed": {"superseded", "invalidated"},
    },
}

_EPISTEMIC_TYPES = frozenset({"Evidence", "Assertion", "VerifiedFact", "Inference", "Memory"})
_ACTOR_TYPES = frozenset({"Person", "LicenseHolder", "ServicePrincipal"})
_REFERENCE_RULES: dict[str, dict[str, frozenset[str]]] = {
    "Person": {"endpointIds": frozenset({"ContactEndpoint"})},
    "LicenseHolder": {
        "personId": frozenset({"Person"}),
        "sponsoringBrokerageId": frozenset({"Brokerage"}),
    },
    "BuyerJourney": {
        "buyingPartyId": frozenset({"BuyingParty"}),
        "ownerLicenseHolderId": frozenset({"LicenseHolder"}),
        "leadSourceId": frozenset({"LeadSource"}),
        "propertyReferenceIds": frozenset({"PropertyReference"}),
    },
    "Conversation": {
        "primaryJourneyId": frozenset({"BuyerJourney"}),
        "linkedJourneyIds": frozenset({"BuyerJourney"}),
    },
    "Message": {
        "conversationId": frozenset({"Conversation"}),
        "bodyArtifactId": frozenset({"DocumentArtifact"}),
    },
    "ConsentGrant": {
        "personId": frozenset({"Person"}),
        "endpointId": frozenset({"ContactEndpoint"}),
    },
    "LeadSource": {"evidenceId": frozenset({"Evidence"})},
    "Suppression": {
        "subjectId": frozenset({"Person", "ContactEndpoint"}),
        "endpointId": frozenset({"ContactEndpoint"}),
    },
    "QualificationObservation": {
        "journeyId": frozenset({"BuyerJourney"}),
        "criterionId": frozenset({"QualificationCriterion"}),
        "epistemicItemId": _EPISTEMIC_TYPES,
    },
    "BuyerRequirement": {
        "journeyId": frozenset({"BuyerJourney"}),
        "statedByPersonId": frozenset({"Person"}),
        "assertionId": frozenset({"Assertion"}),
    },
    "FinancingReadiness": {
        "journeyId": frozenset({"BuyerJourney"}),
    },
    "Appointment": {
        "journeyId": frozenset({"BuyerJourney"}),
        "propertyReferenceId": frozenset({"PropertyReference"}),
    },
    "Commitment": {"journeyId": frozenset({"BuyerJourney"})},
    "IabsDelivery": {
        "responsibleLicenseHolderId": frozenset({"LicenseHolder"}),
        "brokerageId": frozenset({"Brokerage"}),
        "recipientPersonId": frozenset({"Person"}),
        "propertyReferenceId": frozenset({"PropertyReference"}),
        "artifactId": frozenset({"DocumentArtifact"}),
        "evidenceIds": frozenset({"Evidence"}),
    },
    "WrittenBuyerAgreement": {
        "brokerPartyId": frozenset({"Brokerage"}),
        "responsibleLicenseHolderId": frozenset({"LicenseHolder"}),
        "buyerPartyIds": frozenset({"BuyingParty"}),
        "executedArtifactId": frozenset({"DocumentArtifact"}),
        "propertyReferenceIds": frozenset({"PropertyReference"}),
    },
    "AgreementQualification": {
        "buyerPartyId": frozenset({"BuyingParty"}),
        "responsibleLicenseHolderId": frozenset({"LicenseHolder"}),
        "brokerageId": frozenset({"Brokerage"}),
        "propertyReferenceId": frozenset({"PropertyReference"}),
        "agreementId": frozenset({"WrittenBuyerAgreement"}),
        "iabsDeliveryId": frozenset({"IabsDelivery"}),
    },
    "RepresentationRelationship": {
        "brokerageId": frozenset({"Brokerage"}),
        "buyingPartyId": frozenset({"BuyingParty"}),
        "agreementId": frozenset({"WrittenBuyerAgreement"}),
    },
    "Transaction": {
        "journeyId": frozenset({"BuyerJourney"}),
        "buyingPartyId": frozenset({"BuyingParty"}),
        "brokerageId": frozenset({"Brokerage"}),
        "propertyReferenceId": frozenset({"PropertyReference"}),
        "executedArtifactId": frozenset({"DocumentArtifact"}),
        "confirmedDateIds": frozenset({"ConfirmedTransactionDate"}),
    },
    "TransactionMilestone": {
        "transactionId": frozenset({"Transaction"}),
        "confirmationEvidenceId": frozenset({"Evidence"}),
    },
    "Authorization": {"revocationEvidenceId": frozenset({"Evidence"})},
    "VerifiedFact": {"supportingEvidenceIds": frozenset({"Evidence"})},
    "Inference": {"inputItemIds": _EPISTEMIC_TYPES},
    "Memory": {"sourceItemIds": _EPISTEMIC_TYPES},
    "Contradiction": {
        "leftItemId": _EPISTEMIC_TYPES,
        "rightItemId": _EPISTEMIC_TYPES,
        "resolutionItemId": _EPISTEMIC_TYPES,
    },
    "Correction": {
        "correctedItemId": _EPISTEMIC_TYPES,
        "replacementItemId": _EPISTEMIC_TYPES,
        "correctionEvidenceIds": frozenset({"Evidence"}),
    },
    "ConnectorGrant": {},
    "ConfirmedTransactionDate": {"transactionId": frozenset({"Transaction"})},
}


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("canonical timestamp must include an offset")
    return parsed.astimezone(UTC)


def validate_reference_graph(
    record: dict[str, Any], resolver: Callable[[str], dict[str, Any] | None]
) -> None:
    """Resolve ontology-declared references in the same tenant and enforce target types."""
    violations: list[Violation] = []

    def require(reference_id: str, allowed: frozenset[str], path: str) -> None:
        target = resolver(reference_id)
        if target is None:
            violations.append(Violation("REFERENCE_NOT_FOUND", path, reference_id))
        elif target.get("tenantId") != record["tenantId"]:
            violations.append(Violation("CROSS_TENANT_REFERENCE", path, reference_id))
        elif target.get("recordType") not in allowed:
            violations.append(
                Violation(
                    "REFERENCE_TYPE_MISMATCH",
                    path,
                    f"expected {sorted(allowed)}, got {target.get('recordType')}",
                )
            )

    if record["recordType"] != "Evidence":
        for index, reference_id in enumerate(record.get("sourceEvidenceIds", [])):
            require(str(reference_id), frozenset({"Evidence"}), f"$.sourceEvidenceIds.{index}")

    created_by = record.get("createdBy")
    if isinstance(created_by, dict) and created_by.get("actorType") != "system_migration":
        require(
            str(created_by["actorId"]),
            _canonical_types_for_label(created_by["actorType"]),
            "$.createdBy.actorId",
        )

    attributed_to = record.get("attributedTo")
    if isinstance(attributed_to, dict) and attributed_to.get("actorType") != "system_migration":
        require(
            str(attributed_to["actorId"]),
            _canonical_types_for_label(attributed_to["actorType"]),
            "$.attributedTo.actorId",
        )

    for field_name, allowed in _REFERENCE_RULES.get(record["recordType"], {}).items():
        value = record.get(field_name)
        if value is None:
            continue
        values = value if isinstance(value, list) else [value]
        for index, reference_id in enumerate(values):
            suffix = f".{index}" if isinstance(value, list) else ""
            require(str(reference_id), allowed, f"$.{field_name}{suffix}")

    artifact_bindings = {
        "IabsDelivery": ("artifactId", "artifactDigest"),
        "WrittenBuyerAgreement": ("executedArtifactId", "executedArtifactDigest"),
        "Transaction": ("executedArtifactId", "executedArtifactDigest"),
    }
    binding = artifact_bindings.get(record["recordType"])
    if binding is not None:
        artifact_field, digest_field = binding
        artifact = resolver(str(record[artifact_field]))
        if (
            artifact is not None
            and artifact.get("recordType") == "DocumentArtifact"
            and artifact.get("tenantId") == record["tenantId"]
            and artifact.get("digest") != record[digest_field]
        ):
            violations.append(
                Violation(
                    "ARTIFACT_DIGEST_MISMATCH",
                    f"$.{digest_field}",
                    "digest does not match the referenced DocumentArtifact",
                )
            )

    if record["recordType"] == "Transaction":
        for index, reference_id in enumerate(record.get("confirmedDateIds", [])):
            confirmed_date = resolver(str(reference_id))
            if (
                confirmed_date is not None
                and confirmed_date.get("recordType") == "ConfirmedTransactionDate"
                and confirmed_date.get("tenantId") == record["tenantId"]
            ):
                if confirmed_date.get("transactionId") != record["id"]:
                    violations.append(
                        Violation(
                            "TRANSACTION_DATE_OWNER_MISMATCH",
                            f"$.confirmedDateIds.{index}",
                            "confirmed date belongs to another transaction",
                        )
                    )
                if confirmed_date.get("confirmationState") != "confirmed":
                    violations.append(
                        Violation(
                            "TRANSACTION_DATE_NOT_CONFIRMED",
                            f"$.confirmedDateIds.{index}",
                            "transaction may reference only confirmed dates",
                        )
                    )

    if record["recordType"] in {"Assertion", "VerifiedFact", "Inference"}:
        applicable_journey_id = record["proposition"].get("applicableJourneyId")
        if applicable_journey_id is not None:
            require(
                str(applicable_journey_id),
                frozenset({"BuyerJourney"}),
                "$.proposition.applicableJourneyId",
            )
    if record["recordType"] == "Assertion" and record["speakerType"] != "external_party":
        require(
            str(record["speakerId"]),
            _canonical_types_for_label(record["speakerType"]),
            "$.speakerId",
        )
    if record["recordType"] == "Approval" and record["approverType"] in {
        "person",
        "license_holder",
    }:
        require(
            str(record["approverId"]),
            _canonical_types_for_label(record["approverType"]),
            "$.approverId",
        )
    if record["recordType"] == "Evidence" and record["sourceType"] in {
        "message",
        "document_artifact",
    }:
        evidence_source_types = {
            "message": frozenset({"Message"}),
            "document_artifact": frozenset({"DocumentArtifact"}),
        }
        require(
            str(record["sourceRef"]),
            evidence_source_types[record["sourceType"]],
            "$.sourceRef",
        )

    typed_collections: list[tuple[str, list[dict[str, Any]], str, str]] = []
    if record["recordType"] == "BuyingParty":
        typed_collections.append(("members", record["members"], "personId", "person"))
    elif record["recordType"] == "Conversation":
        typed_collections.append(
            ("participants", record["participants"], "participantId", "participantType")
        )
    elif record["recordType"] == "Message":
        require(str(record["senderId"]), _actor_types_for_label(record["senderType"]), "$.senderId")
        for index, item in enumerate(record["recipients"]):
            require(
                str(item["recipientId"]),
                _actor_types_for_label(item["recipientType"]),
                f"$.recipients.{index}.recipientId",
            )
    elif record["recordType"] == "ContactEndpoint":
        require(str(record["ownerId"]), _actor_types_for_label(record["ownerType"]), "$.ownerId")
    elif record["recordType"] == "Commitment":
        for index, reference_id in enumerate(record["beneficiaryIds"]):
            require(
                str(reference_id), frozenset({"Person", "BuyingParty"}), f"$.beneficiaryIds.{index}"
            )
    elif record["recordType"] == "Authorization":
        require(
            str(record["grantorId"]),
            _canonical_types_for_label(record["grantorType"]),
            "$.grantorId",
        )
        require(
            str(record["granteeId"]),
            _canonical_types_for_label(record["granteeType"]),
            "$.granteeId",
        )
    elif record["recordType"] == "ConnectorGrant":
        require(
            str(record["delegatedPrincipalId"]),
            _canonical_types_for_label(record["delegatedPrincipalType"]),
            "$.delegatedPrincipalId",
        )
        require(
            str(record["grantorId"]),
            _canonical_types_for_label(record["grantorType"]),
            "$.grantorId",
        )
        if record["grantState"] == "revoked":
            require(
                str(record["revocationEvidenceId"]),
                frozenset({"Evidence"}),
                "$.revocationEvidenceId",
            )
    elif record["recordType"] == "ConfirmedTransactionDate":
        source = resolver(str(record["confirmationSourceId"]))
        transaction = resolver(str(record["transactionId"]))
        require(
            str(record["confirmationSourceId"]),
            frozenset({record["confirmationSourceType"]}),
            "$.confirmationSourceId",
        )
        if (
            source is not None
            and source.get("tenantId") == record["tenantId"]
            and source.get("digest") != record["confirmationSourceDigest"]
        ):
            violations.append(
                Violation(
                    "CONFIRMATION_SOURCE_DIGEST_MISMATCH",
                    "$.confirmationSourceDigest",
                    "digest does not match confirmation source",
                )
            )
        if (
            transaction is not None
            and transaction.get("recordType") == "Transaction"
            and transaction.get("tenantId") == record["tenantId"]
        ):
            if record["confirmationSourceType"] == "DocumentArtifact":
                bound = (
                    transaction.get("executedArtifactId") == record["confirmationSourceId"]
                    and transaction.get("executedArtifactDigest")
                    == record["confirmationSourceDigest"]
                )
            else:
                bound = record["confirmationSourceId"] in transaction.get("sourceEvidenceIds", [])
            if not bound:
                violations.append(
                    Violation(
                        "TRANSACTION_DATE_SOURCE_UNBOUND",
                        "$.confirmationSourceId",
                        "confirmation source is not the transaction artifact or cited evidence",
                    )
                )
    elif record["recordType"] == "Memory":
        memory_scope_types = {
            "conversation": frozenset({"Conversation"}),
            "journey": frozenset({"BuyerJourney"}),
            "buying_party": frozenset({"BuyingParty"}),
            "person": frozenset({"Person"}),
        }
        require(str(record["scopeId"]), memory_scope_types[record["scopeType"]], "$.scopeId")
    elif record["recordType"] == "Contradiction" and record["scopeType"] in {
        "journey",
        "person",
        "agreement",
        "authority",
    }:
        contradiction_scope_types = {
            "journey": frozenset({"BuyerJourney"}),
            "person": frozenset({"Person"}),
            "agreement": frozenset({"WrittenBuyerAgreement"}),
            "authority": frozenset({"Authorization"}),
        }
        require(
            str(record["scopeId"]),
            contradiction_scope_types[record["scopeType"]],
            "$.scopeId",
        )
    elif record["recordType"] == "WrittenBuyerAgreement":
        for index, item in enumerate(record["signatureEvidence"]):
            require(
                str(item["signerPartyId"]),
                frozenset({"Brokerage", "BuyingParty"}),
                f"$.signatureEvidence.{index}.signerPartyId",
            )
            require(
                str(item["evidenceId"]),
                frozenset({"Evidence"}),
                f"$.signatureEvidence.{index}.evidenceId",
            )

    type_labels = {
        "person": frozenset({"Person"}),
        "license_holder": frozenset({"LicenseHolder"}),
        "service_principal": frozenset({"ServicePrincipal"}),
    }
    for collection_name, items, id_field, type_field in typed_collections:
        for index, item in enumerate(items):
            allowed = (
                frozenset({"Person"}) if type_field == "person" else type_labels[item[type_field]]
            )
            require(str(item[id_field]), allowed, f"$.{collection_name}.{index}.{id_field}")

    if violations:
        raise ContractViolation(violations)


def _actor_types_for_label(label: str) -> frozenset[str]:
    return {
        "person": frozenset({"Person"}),
        "license_holder": frozenset({"LicenseHolder"}),
        "service_principal": frozenset({"ServicePrincipal"}),
        "endpoint": frozenset({"ContactEndpoint"}),
    }.get(label, _ACTOR_TYPES)


def _canonical_types_for_label(label: str) -> frozenset[str]:
    return {
        "person": frozenset({"Person"}),
        "license_holder": frozenset({"LicenseHolder"}),
        "service_principal": frozenset({"ServicePrincipal"}),
        "brokerage": frozenset({"Brokerage"}),
        "tenant": frozenset({"Tenant"}),
    }.get(label, frozenset())


def validate_verified_fact_admission(
    fact: dict[str, Any],
    evidence: Sequence[dict[str, Any]],
    verifier: FactVerifier | None,
) -> None:
    """Require a configured predicate rule over the exact declared evidence set."""
    if verifier is None:
        raise ContractViolation(
            [
                Violation(
                    "VERIFICATION_RULE_UNAVAILABLE",
                    "$.verificationRuleId",
                    "no governed predicate verifier is configured",
                )
            ]
        )
    expected = list(fact["supportingEvidenceIds"])
    actual = [item["id"] for item in evidence]
    if actual != expected:
        raise ContractViolation(
            [
                Violation(
                    "VERIFICATION_EVIDENCE_MISMATCH",
                    "$.supportingEvidenceIds",
                    "loaded evidence does not exactly match the declared ordered set",
                )
            ]
        )
    if not verifier.verify(fact["verificationRuleId"], fact["proposition"], evidence):
        raise ContractViolation(
            [
                Violation(
                    "FACT_VERIFICATION_FAILED",
                    "$.verificationRuleId",
                    "predicate-specific verification rule denied admission",
                )
            ]
        )


def validate_update(previous: dict[str, Any], current: dict[str, Any]) -> None:
    """Enforce invariants that JSON Schema cannot express across record versions."""
    violations: list[Violation] = []
    immutable = ("id", "tenantId", "schemaVersion", "recordType", "createdAt", "createdBy")
    for field_name in immutable:
        if previous[field_name] != current[field_name]:
            violations.append(
                Violation(
                    "IMMUTABLE_CANONICAL_FIELD",
                    f"$.{field_name}",
                    "cannot change across versions",
                )
            )
    if current["version"] != previous["version"] + 1:
        violations.append(
            Violation("NON_MONOTONIC_VERSION", "$.version", "must advance by exactly one")
        )
    if _time(current["updatedAt"]) < _time(previous["updatedAt"]):
        violations.append(Violation("UPDATED_AT_REGRESSION", "$.updatedAt", "cannot move backward"))
    if _time(current["effectiveFrom"]) < _time(previous["effectiveFrom"]):
        violations.append(
            Violation("EFFECTIVE_TIME_REGRESSION", "$.effectiveFrom", "cannot move backward")
        )

    old_status, new_status = previous["status"], current["status"]
    if old_status != new_status and new_status not in _UNIVERSAL_STATUS_TRANSITIONS[old_status]:
        violations.append(
            Violation(
                "INVALID_STATUS_TRANSITION",
                "$.status",
                f"{old_status} cannot transition to {new_status}",
            )
        )

    for field_name in _STATE_FIELDS.get(current["recordType"], ()):
        old_state, new_state = previous[field_name], current[field_name]
        declared = _DECLARED_TRANSITIONS[(current["recordType"], field_name)]
        if old_state != new_state and new_state not in declared.get(old_state, set()):
            violations.append(
                Violation(
                    "INVALID_STATE_TRANSITION",
                    f"$.{field_name}",
                    f"{old_state} cannot transition to {new_state}",
                )
            )

    if (
        current["recordType"] == "AgreementQualification"
        and previous["result"] != current["result"]
    ):
        violations.append(
            Violation(
                "IMMUTABLE_QUALIFICATION_RESULT",
                "$.result",
                "qualification results are immutable; reevaluation creates a new record",
            )
        )

    if current["recordType"] == "Approval":
        for field_name in (
            "approverType",
            "approverId",
            "actionClass",
            "actionIntentId",
            "payloadDigest",
            "scope",
            "decision",
        ):
            if previous[field_name] != current[field_name]:
                violations.append(
                    Violation(
                        "IMMUTABLE_APPROVAL_BINDING",
                        f"$.{field_name}",
                        "approval decisions and exact-payload bindings are immutable",
                    )
                )

    if current["recordType"] in {"Assertion", "VerifiedFact", "Inference"}:
        for field_name in ("proposition",):
            if previous[field_name] != current[field_name]:
                violations.append(
                    Violation(
                        "EPISTEMIC_IDENTITY_MUTATION",
                        f"$.{field_name}",
                        "correction requires an explicit replacement item",
                    )
                )
    if current["recordType"] == "EffectAttempt":
        for field_name in (
            "intentId",
            "actionClass",
            "payloadDigest",
            "permitDigest",
            "idempotencyKey",
        ):
            if previous[field_name] != current[field_name]:
                violations.append(
                    Violation(
                        "EFFECT_IDENTITY_MUTATION",
                        f"$.{field_name}",
                        "effect identity and exact payload binding are immutable",
                    )
                )
    if violations:
        raise ContractViolation(violations)


def validate_representation_relationship(
    relationship: dict[str, Any], agreement: dict[str, Any] | None
) -> None:
    """An active relationship can only derive from one covering representation agreement."""
    if relationship["relationshipState"] != "active":
        return
    violations: list[Violation] = []
    if agreement is None or agreement.get("recordType") != "WrittenBuyerAgreement":
        violations.append(
            Violation("REPRESENTATION_AGREEMENT_MISSING", "$.agreementId", "agreement not found")
        )
    else:
        if agreement["agreementType"] != "representation":
            violations.append(
                Violation(
                    "NON_REPRESENTATION_CANNOT_REPRESENT",
                    "$.agreementId",
                    "showing-only agreement cannot create representation",
                )
            )
        if agreement["executionState"] != "effective":
            violations.append(
                Violation(
                    "REPRESENTATION_AGREEMENT_NOT_EFFECTIVE",
                    "$.agreementId",
                    "representation agreement must be effective",
                )
            )
        if agreement["brokerPartyId"] != relationship["brokerageId"]:
            violations.append(
                Violation(
                    "REPRESENTATION_BROKER_MISMATCH", "$.brokerageId", "does not match agreement"
                )
            )
        if relationship["buyingPartyId"] not in agreement["buyerPartyIds"]:
            violations.append(
                Violation(
                    "REPRESENTATION_BUYER_MISMATCH",
                    "$.buyingPartyId",
                    "is not a party to the agreement",
                )
            )
        effective = _time(relationship["effectiveFrom"])
        if not (_time(agreement["effectiveAt"]) <= effective < _time(agreement["terminatesAt"])):
            violations.append(
                Violation(
                    "REPRESENTATION_OUTSIDE_AGREEMENT_TERM",
                    "$.effectiveFrom",
                    "must fall within the agreement term",
                )
            )
    if violations:
        raise ContractViolation(violations)


def validate_agreement_qualification(
    qualification: dict[str, Any], agreement: dict[str, Any] | None
) -> None:
    """Validate a qualified agreement result against the exact current agreement version."""
    if qualification["result"] != "qualified" or "agreementId" not in qualification:
        return
    violations: list[Violation] = []
    if agreement is None or agreement.get("recordType") != "WrittenBuyerAgreement":
        violations.append(
            Violation("QUALIFYING_AGREEMENT_MISSING", "$.agreementId", "agreement not found")
        )
    else:
        if agreement["id"] != qualification["agreementId"]:
            violations.append(
                Violation(
                    "QUALIFYING_AGREEMENT_MISMATCH",
                    "$.agreementId",
                    "does not match loaded agreement",
                )
            )
        if agreement["version"] != qualification.get("agreementVersion"):
            violations.append(
                Violation(
                    "QUALIFYING_AGREEMENT_VERSION",
                    "$.agreementVersion",
                    "must match the current agreement version",
                )
            )
        if agreement["executionState"] != "effective":
            violations.append(
                Violation(
                    "QUALIFYING_AGREEMENT_NOT_EFFECTIVE",
                    "$.agreementId",
                    "agreement must be effective",
                )
            )
        if agreement["brokerPartyId"] != qualification["brokerageId"]:
            violations.append(
                Violation("QUALIFYING_BROKER_MISMATCH", "$.brokerageId", "does not match agreement")
            )
        if agreement["responsibleLicenseHolderId"] != qualification["responsibleLicenseHolderId"]:
            violations.append(
                Violation(
                    "QUALIFYING_LICENSE_HOLDER_MISMATCH",
                    "$.responsibleLicenseHolderId",
                    "does not match agreement",
                )
            )
        if qualification["buyerPartyId"] not in agreement["buyerPartyIds"]:
            violations.append(
                Violation(
                    "QUALIFYING_BUYER_MISMATCH",
                    "$.buyerPartyId",
                    "is not a party to the agreement",
                )
            )
        evaluated = _time(qualification["evaluatedAt"])
        if not (_time(agreement["effectiveAt"]) <= evaluated < _time(agreement["terminatesAt"])):
            violations.append(
                Violation(
                    "QUALIFICATION_OUTSIDE_AGREEMENT_TERM",
                    "$.evaluatedAt",
                    "must fall within the agreement term",
                )
            )
        services = {
            item["serviceCode"] for item in agreement["serviceDefinitions"] if item["allowed"]
        }
        action = qualification["actionType"]
        if action == "residential_offer_presentation":
            if (
                agreement["agreementType"] != "representation"
                or "offer_presentation" not in services
            ):
                violations.append(
                    Violation(
                        "OFFER_NOT_COVERED_BY_AGREEMENT",
                        "$.actionType",
                        "offer presentation requires a covering representation agreement",
                    )
                )
        elif "showing_access" not in services:
            violations.append(
                Violation(
                    "SHOWING_NOT_COVERED_BY_AGREEMENT",
                    "$.actionType",
                    "residential showing requires showing access service",
                )
            )
    if violations:
        raise ContractViolation(violations)
