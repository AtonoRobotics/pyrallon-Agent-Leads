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
            if _time(window["notBefore"]) >= _time(window["expiresAt"]):
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
    if violations:
        raise ContractViolation(violations)

