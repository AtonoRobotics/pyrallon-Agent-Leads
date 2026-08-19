from datetime import UTC, datetime
from typing import Any

from .errors import ContractViolation, Violation


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include an offset")
    return parsed.astimezone(UTC)


def validate_authority_activation_fair_housing_semantics(
    record: dict[str, Any], *, now: datetime | None = None
) -> None:
    """Validate cross-field rules for OPEN-025–027 closure records."""
    violations: list[Violation] = []
    current = (now or datetime.now(UTC)).astimezone(UTC)
    record_type = record.get("recordType")

    if record_type == "ActorTenantAuthorization":
        if _timestamp(record["effectiveAt"]) >= _timestamp(record["expiresAt"]):
            violations.append(
                Violation("AUTHORIZATION_WINDOW", "$.expiresAt", "must follow effectiveAt")
            )
        if record["status"] == "active" and _timestamp(record["expiresAt"]) <= current:
            violations.append(
                Violation("AUTHORIZATION_EXPIRED", "$.expiresAt", "active authorization is expired")
            )
        if record["status"] == "active" and _timestamp(record["effectiveAt"]) > current:
            violations.append(
                Violation(
                    "AUTHORIZATION_NOT_EFFECTIVE",
                    "$.effectiveAt",
                    "active authorization is not yet effective",
                )
            )
        if record["status"] == "revoked" and not record.get("revokedAt"):
            violations.append(
                Violation(
                    "AUTHORIZATION_REVOCATION", "$.revokedAt", "revoked status requires revokedAt"
                )
            )

    if record_type == "ReleaseActivation":
        if _timestamp(record["effectiveAt"]) >= _timestamp(record["expiresAt"]):
            violations.append(
                Violation("ACTIVATION_WINDOW", "$.expiresAt", "must follow effectiveAt")
            )
        if record["status"] == "active" and _timestamp(record["expiresAt"]) <= current:
            violations.append(
                Violation("ACTIVATION_EXPIRED", "$.expiresAt", "active activation is expired")
            )
        if record["status"] == "active" and _timestamp(record["effectiveAt"]) > current:
            violations.append(
                Violation(
                    "ACTIVATION_NOT_EFFECTIVE",
                    "$.effectiveAt",
                    "active activation is not yet effective",
                )
            )
        if record["status"] == "revoked" and not record.get("revokedAt"):
            violations.append(
                Violation(
                    "ACTIVATION_REVOCATION", "$.revokedAt", "revoked status requires revokedAt"
                )
            )
        if "outbound_ai_voice" in record["enabledCapabilities"]:
            violations.append(
                Violation(
                    "PROHIBITED_CAPABILITY",
                    "$.enabledCapabilities",
                    "outbound AI voice cannot be activated",
                )
            )
        evidence = {item["gateId"]: item for item in record["gateEvidence"]}
        missing = set(record["requiredGateIds"]) - set(evidence)
        if missing:
            violations.append(
                Violation(
                    "MISSING_GATE_EVIDENCE", "$.gateEvidence", f"missing gates: {sorted(missing)}"
                )
            )
        for index, item in enumerate(record["gateEvidence"]):
            if item["gateId"] in record["requiredGateIds"] and item["outcome"] != "pass":
                violations.append(
                    Violation(
                        "NONPASSING_REQUIRED_GATE",
                        f"$.gateEvidence.{index}.outcome",
                        "required gate must pass",
                    )
                )
            if (
                item["applicability"] == "platform_invariant"
                and item["outcome"] == "not_applicable"
            ):
                violations.append(
                    Violation(
                        "PLATFORM_GATE_NOT_APPLICABLE",
                        f"$.gateEvidence.{index}.outcome",
                        "platform invariant cannot be not_applicable",
                    )
                )
            if item["outcome"] == "pass" and _timestamp(item["expiresAt"]) <= current:
                violations.append(
                    Violation(
                        "GATE_EVIDENCE_EXPIRED",
                        f"$.gateEvidence.{index}.expiresAt",
                        "passing gate evidence is expired",
                    )
                )

    if record_type == "FairHousingControlProfile":
        feature_ids = [item["featureId"] for item in record["allowedFeatures"]]
        if len(feature_ids) != len(set(feature_ids)):
            violations.append(
                Violation(
                    "DUPLICATE_ALLOWED_FEATURE",
                    "$.allowedFeatures",
                    "featureId values must be unique",
                )
            )
        normalized_tokens = [item.casefold() for item in record["protectedTokens"]]
        if len(normalized_tokens) != len(set(normalized_tokens)):
            violations.append(
                Violation(
                    "DUPLICATE_PROTECTED_TOKEN",
                    "$.protectedTokens",
                    "tokens must be unique after case folding",
                )
            )

    if record_type == "FairHousingCounterfactualCase":
        equal = record["baselineOutcomeDigest"] == record["counterfactualOutcomeDigest"]
        if record["outcome"] == "pass" and not equal:
            violations.append(
                Violation(
                    "COUNTERFACTUAL_MISMATCH",
                    "$.counterfactualOutcomeDigest",
                    "passing case requires invariant outcome digest",
                )
            )
        if record["outcome"] == "fail" and equal:
            violations.append(
                Violation(
                    "COUNTERFACTUAL_FALSE_FAILURE",
                    "$.outcome",
                    "equal invariant outcomes cannot be marked fail",
                )
            )

    if violations:
        raise ContractViolation(violations)
