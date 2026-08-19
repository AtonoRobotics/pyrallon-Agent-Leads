from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .errors import ContractViolation, Violation


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include an offset")
    return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class SemanticPolicy:
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    max_proposal_ttl: Mapping[str, timedelta] = field(default_factory=dict)
    supported_proposal_versions: frozenset[str] = frozenset({"cognitive-proposal/1.1.0"})
    clock_skew: timedelta = timedelta(seconds=30)


def validate_semantics(record: dict[str, Any], policy: SemanticPolicy | None = None) -> None:
    if record.get("schemaVersion") in {
        "open-019-024/1.0.0",
        "open-019-024/1.1.0",
    }:
        from .closure import validate_closure_semantics

        validate_closure_semantics(record, now=(policy.now if policy else None))
        return
    active = policy or SemanticPolicy()
    violations: list[Violation] = []
    record_type = record.get("recordType")

    def ordered(before: str, after: str, *, strict: bool = False) -> None:
        if before not in record or after not in record:
            return
        left, right = _time(record[before]), _time(record[after])
        valid = left < right if strict else left <= right
        if not valid:
            violations.append(Violation("TEMPORAL_ORDER", f"$.{after}", f"must follow {before}"))

    ordered("createdAt", "updatedAt")
    ordered("effectiveFrom", "effectiveTo")
    ordered("proposedAt", "expiresAt", strict=True)
    ordered("evaluatedAt", "expiresAt", strict=True)
    ordered("startsAt", "endsAt", strict=True)
    ordered("effectiveAt", "terminatesAt", strict=True)
    ordered("activeFrom", "activeTo", strict=True)
    ordered("grantedAt", "expiresAt", strict=True)
    ordered("grantedAt", "revokedAt")
    ordered("assessedAt", "freshUntil", strict=True)
    ordered("derivedAt", "expiresAt", strict=True)
    ordered("decidedAt", "expiresAt", strict=True)
    ordered("validFrom", "validTo", strict=True)

    externally_consequential = {
        "ConsentGrant",
        "Suppression",
        "Appointment",
        "IabsDelivery",
        "WrittenBuyerAgreement",
        "AgreementQualification",
        "RepresentationRelationship",
        "Transaction",
        "TransactionMilestone",
        "Authorization",
        "Approval",
        "EffectAttempt",
        "ConnectorGrant",
        "ConfirmedTransactionDate",
        "VerifiedFact",
        "Correction",
    }
    if record_type in externally_consequential and not record.get("sourceEvidenceIds"):
        violations.append(
            Violation(
                "SOURCE_EVIDENCE_REQUIRED",
                "$.sourceEvidenceIds",
                "externally consequential canonical records require source evidence",
            )
        )

    if record_type == "CognitiveWorkRequest":
        packet = record["contextPacket"]
        if record["contextManifestId"] != packet["manifestId"]:
            violations.append(
                Violation(
                    "CONTEXT_MANIFEST_MISMATCH",
                    "$.contextManifestId",
                    "must equal contextPacket.manifestId",
                )
            )
        if record["requiredProposalSchemaVersion"] not in active.supported_proposal_versions:
            violations.append(
                Violation(
                    "UNSUPPORTED_PROPOSAL_VERSION",
                    "$.requiredProposalSchemaVersion",
                    "is not supported",
                )
            )
        if _time(packet["compiledAt"]) >= _time(packet["expiresAt"]):
            violations.append(
                Violation(
                    "CONTEXT_EXPIRED_AT_COMPILE",
                    "$.contextPacket.expiresAt",
                    "must follow compiledAt",
                )
            )
        if _time(packet["expiresAt"]) <= active.now:
            violations.append(
                Violation(
                    "STALE_CONTEXT",
                    "$.contextPacket.expiresAt",
                    "context packet is expired",
                )
            )

    if record_type == "CognitiveProposal":
        claim_ids = [claim["claimId"] for claim in record["claims"]]
        if len(claim_ids) != len(set(claim_ids)):
            violations.append(
                Violation("DUPLICATE_CLAIM_ID", "$.claims", "claimId values must be unique")
            )
        proposal_ids = [action["proposalId"] for action in record["proposedActions"]]
        if len(proposal_ids) != len(set(proposal_ids)):
            violations.append(
                Violation(
                    "DUPLICATE_ACTION_ID", "$.proposedActions", "proposalId values must be unique"
                )
            )
        known_claims = set(claim_ids)
        proposal_expiry = _time(record["expiresAt"])
        proposed_at = _time(record["proposedAt"])
        runtime_evidence = record["runtimeEvidence"]
        runtime_started = _time(runtime_evidence["startedAt"])
        runtime_completed = _time(runtime_evidence["completedAt"])
        if runtime_completed < runtime_started:
            violations.append(
                Violation(
                    "RUNTIME_EVIDENCE_ORDER",
                    "$.runtimeEvidence.completedAt",
                    "runtime completion must not precede runtime start",
                )
            )
        if runtime_completed > proposed_at:
            violations.append(
                Violation(
                    "RUNTIME_EVIDENCE_AFTER_PROPOSAL",
                    "$.runtimeEvidence.completedAt",
                    "runtime completion cannot postdate proposal generation",
                )
            )
        for index, claim in enumerate(record["claims"]):
            if _time(claim["freshnessAt"]) > proposed_at:
                violations.append(
                    Violation(
                        "CLAIM_FRESHNESS_AFTER_PROPOSAL",
                        f"$.claims.{index}.freshnessAt",
                        "claim freshness cannot postdate proposal generation",
                    )
                )
        for index, action in enumerate(record["proposedActions"]):
            unknown = set(action["sourceClaimIds"]) - known_claims
            if unknown:
                violations.append(
                    Violation(
                        "UNRESOLVED_SOURCE_CLAIM",
                        f"$.proposedActions.{index}.sourceClaimIds",
                        f"unknown claims: {sorted(unknown)}",
                    )
                )
            window = action["requestedExecutionWindow"]
            if "notBefore" in window and _time(window["notBefore"]) >= _time(window["expiresAt"]):
                violations.append(
                    Violation(
                        "INVALID_EXECUTION_WINDOW",
                        f"$.proposedActions.{index}.requestedExecutionWindow",
                        "notBefore must precede expiresAt",
                    )
                )
            if _time(window["expiresAt"]) > proposal_expiry:
                violations.append(
                    Violation(
                        "ACTION_OUTLIVES_PROPOSAL",
                        f"$.proposedActions.{index}.requestedExecutionWindow.expiresAt",
                        "must not exceed proposal expiry",
                    )
                )
        ttl = active.max_proposal_ttl.get(record["actionClass"])
        if ttl is not None and proposal_expiry - _time(record["proposedAt"]) > ttl:
            violations.append(
                Violation("PROPOSAL_TTL_EXCEEDED", "$.expiresAt", "exceeds action-class policy")
            )
        if proposal_expiry <= active.now - active.clock_skew:
            violations.append(Violation("STALE_PROPOSAL", "$.expiresAt", "proposal is expired"))

    if record_type == "WrittenBuyerAgreement":
        effective, terminates = _time(record["effectiveAt"]), _time(record["terminatesAt"])
        if record["agreementType"] == "non_representation_showing":
            if terminates - effective > timedelta(days=14):
                violations.append(
                    Violation(
                        "NON_REP_TERM_EXCEEDED",
                        "$.terminatesAt",
                        "showing-only agreement may not exceed 14 days",
                    )
                )
            services = {
                item["serviceCode"]: item["allowed"] for item in record["serviceDefinitions"]
            }
            if services.get("showing_access") is not True or any(
                value for key, value in services.items() if key != "showing_access"
            ):
                violations.append(
                    Violation(
                        "NON_REP_SERVICE_SCOPE",
                        "$.serviceDefinitions",
                        "only showing_access may be allowed",
                    )
                )
        if record["executionState"] in {"executed", "effective"}:
            compensation = record["compensation"]
            if (
                not compensation["objectivelyAscertainable"]
                or not compensation["negotiabilityDisclosurePresent"]
            ):
                violations.append(
                    Violation(
                        "INVALID_COMPENSATION_DISCLOSURE",
                        "$.compensation",
                        "effective agreement requires ascertainable compensation and negotiability disclosure",
                    )
                )
            signed_parties = {item["signerPartyId"] for item in record["signatureEvidence"]}
            missing_buyers = set(record["buyerPartyIds"]) - signed_parties
            if missing_buyers:
                violations.append(
                    Violation(
                        "MISSING_BUYER_SIGNATURE",
                        "$.signatureEvidence",
                        f"missing signatures for buyer parties: {sorted(missing_buyers)}",
                    )
                )

    if record_type == "AgreementQualification":
        has_agreement = "agreementId" in record
        has_exception = "exceptionCode" in record
        if record["result"] == "qualified" and has_agreement == has_exception:
            violations.append(
                Violation(
                    "QUALIFICATION_BASIS",
                    "$",
                    "qualified result requires exactly one agreement or approved exception",
                )
            )

    if (
        record_type == "Appointment"
        and record["appointmentState"] == "confirmed"
        and (not record.get("providerEventId") or not record.get("providerVersion"))
    ):
        violations.append(
            Violation(
                "CONFIRMED_APPOINTMENT_PROVIDER_REF",
                "$",
                "confirmed appointment requires provider resource id and version",
            )
        )

    if record_type == "VerifiedFact":
        if record.get("verificationMethod", "").startswith("model:"):
            violations.append(
                Violation(
                    "MODEL_CANNOT_VERIFY_FACT",
                    "$.verificationMethod",
                    "model output cannot directly create a verified fact",
                )
            )
        if not record.get("supportingEvidenceIds"):
            violations.append(
                Violation(
                    "EPISTEMIC_INPUT_REQUIRED",
                    "$.supportingEvidenceIds",
                    "verified facts require source evidence links",
                )
            )

    if record_type in {"Inference", "Memory"}:
        link_field = "inputItemIds" if record_type == "Inference" else "sourceItemIds"
        if not record.get(link_field):
            violations.append(
                Violation(
                    "EPISTEMIC_INPUT_REQUIRED",
                    f"$.{link_field}",
                    "derived epistemic records require source item links",
                )
            )

    if record_type == "Correction":
        replacement = record.get("replacementItemId")
        if (record["correctionAction"] == "replace") != bool(replacement):
            violations.append(
                Violation(
                    "CORRECTION_REPLACEMENT",
                    "$.replacementItemId",
                    "replace requires a replacement; invalidate forbids one",
                )
            )

    if (
        record_type == "ConnectorGrant"
        and record["grantState"] == "revoked"
        and (not record.get("revokedAt") or not record.get("revocationEvidenceId"))
    ):
        violations.append(
            Violation(
                "CONNECTOR_GRANT_REVOCATION_EVIDENCE_REQUIRED",
                "$.revocationEvidenceId",
                "revoked grants require a revocation time and evidence",
            )
        )

    if (
        record_type == "Transaction"
        and record.get("transactionState")
        in {
            "closing_pending",
            "closed",
        }
        and not record.get("confirmedDateIds")
    ):
        violations.append(
            Violation(
                "CONFIRMED_TRANSACTION_DATE_REQUIRED",
                "$.confirmedDateIds",
                "closing transactions require at least one ConfirmedTransactionDate",
            )
        )

    if record_type == "Contradiction" and record["leftItemId"] == record["rightItemId"]:
        violations.append(
            Violation("SELF_CONTRADICTION", "$.rightItemId", "contradiction items must be distinct")
        )
    if record_type == "Contradiction":
        resolved = (
            record["resolutionState"].startswith("resolved_")
            or record["resolutionState"] == "dismissed"
        )
        if resolved and "resolvedAt" not in record:
            violations.append(
                Violation(
                    "CONTRADICTION_RESOLUTION_TIME_REQUIRED",
                    "$.resolvedAt",
                    "resolved or dismissed contradictions require resolution time",
                )
            )
        has_resolution = "resolutionItemId" in record
        if (record["resolutionState"] == "resolved_replacement") != has_resolution:
            violations.append(
                Violation(
                    "CONTRADICTION_RESOLUTION_ITEM",
                    "$.resolutionItemId",
                    "only resolved_replacement requires a resolution item",
                )
            )

    if (
        record_type == "BuyerRequirement"
        and "rangeMinimum" in record
        and "rangeMaximum" in record
        and record["rangeMinimum"] > record["rangeMaximum"]
    ):
        violations.append(
            Violation(
                "INVALID_REQUIREMENT_RANGE",
                "$.rangeMaximum",
                "must be greater than or equal to rangeMinimum",
            )
        )

    if (
        record_type == "Authorization"
        and record["authorizationState"] == "active"
        and "revokedAt" in record
    ):
        violations.append(
            Violation(
                "ACTIVE_AUTHORIZATION_REVOKED",
                "$.revokedAt",
                "an active authorization cannot have revocation evidence",
            )
        )

    if (
        record_type == "EffectAttempt"
        and record["attemptState"] in {"confirmed", "reconciled_succeeded"}
        and not record.get("providerReceiptId")
    ):
        violations.append(
            Violation(
                "PROVIDER_RECEIPT_REQUIRED",
                "$.providerReceiptId",
                "external completion requires provider receipt or reconciliation evidence",
            )
        )

    if (
        record_type == "TransactionMilestone"
        and record["confirmationState"] == "confirmed"
        and not record.get("confirmationEvidenceId")
    ):
        violations.append(
            Violation(
                "MILESTONE_CONFIRMATION_EVIDENCE_REQUIRED",
                "$.confirmationEvidenceId",
                "a confirmed transaction date requires confirmation evidence",
            )
        )

    if violations:
        raise ContractViolation(violations)


def validate_gateway_pair(
    request: dict[str, Any], proposal: dict[str, Any], policy: SemanticPolicy | None = None
) -> None:
    validate_semantics(request, policy)
    validate_semantics(proposal, policy)
    violations: list[Violation] = []
    for field_name in ("workId", "actionClass", "contextManifestId"):
        if request[field_name] != proposal[field_name]:
            violations.append(
                Violation(
                    "REQUEST_PROPOSAL_MISMATCH",
                    f"$.{field_name}",
                    "proposal does not match request",
                )
            )
    if request["requiredProposalSchemaVersion"] != proposal["schemaVersion"]:
        violations.append(
            Violation("PROPOSAL_VERSION_MISMATCH", "$.schemaVersion", "does not satisfy request")
        )
    if proposal["runtimeEvidence"]["routePolicyVersion"] != request["routePolicyVersion"]:
        violations.append(
            Violation(
                "RUNTIME_ROUTE_POLICY_MISMATCH",
                "$.runtimeEvidence.routePolicyVersion",
                "runtime evidence does not match the immutable request route policy",
            )
        )
    admitted_sources = {
        source_id
        for section in request["contextPacket"]["sections"]
        for source_id in section["sourceRecordIds"]
    }
    for claim_index, claim in enumerate(proposal["claims"]):
        outside = set(claim["sourceIds"]) - admitted_sources
        if outside:
            violations.append(
                Violation(
                    "CLAIM_SOURCE_OUTSIDE_CONTEXT",
                    f"$.claims.{claim_index}.sourceIds",
                    f"sources not admitted by context: {sorted(outside)}",
                )
            )
    if violations:
        raise ContractViolation(violations)
