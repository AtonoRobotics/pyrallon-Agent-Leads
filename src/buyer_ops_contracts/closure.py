from datetime import UTC, datetime
from typing import Any

from .digest import sha256_digest
from .errors import ContractViolation, Violation


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include an offset")
    return parsed.astimezone(UTC)


def validate_closure_semantics(record: dict[str, Any], *, now: datetime | None = None) -> None:
    """Validate cross-field rules for OPEN-019–024 closure records."""
    violations: list[Violation] = []
    current = (now or datetime.now(UTC)).astimezone(UTC)
    record_type = record.get("recordType")
    if record.get("schemaVersion") == "open-019-024/1.1.0":
        effective_from = _timestamp(record["effectiveFrom"])
        observed_at = _timestamp(record["observedAt"])
        if observed_at > effective_from:
            violations.append(
                Violation(
                    "OBSERVATION_EFFECTIVE_ORDER",
                    "$.effectiveFrom",
                    "effectiveFrom cannot precede observedAt",
                )
            )
        effective_to = record.get("effectiveTo")
        if effective_to is not None and _timestamp(effective_to) <= effective_from:
            violations.append(
                Violation("EFFECTIVE_WINDOW_ORDER", "$.effectiveTo", "must follow effectiveFrom")
            )
        if record["recordVersion"] > 1 and not record.get("supersedesRecordId"):
            violations.append(
                Violation(
                    "SUPERSESSION_REQUIRED",
                    "$.supersedesRecordId",
                    "record versions after one require a predecessor",
                )
            )
        if record["status"] != "current" and effective_to is None:
            violations.append(
                Violation(
                    "LIFECYCLE_END_REQUIRED",
                    "$.effectiveTo",
                    "non-current records require effectiveTo",
                )
            )
        expires_at = record.get("expiresAt")
        if expires_at is not None and _timestamp(expires_at) <= effective_from:
            violations.append(Violation("EXPIRY_ORDER", "$.expiresAt", "must follow effectiveFrom"))
    if record_type == "CapabilityInventory" and record.get("schemaVersion") == "open-019-024/1.1.0":
        capabilities = set(record["capabilities"])
        effect_classes = set(record["effectClasses"])
        mappings = record["capabilityEffects"]
        mapped_capabilities = [mapping["capability"] for mapping in mappings]
        mapped_effects = {
            action_class for mapping in mappings for action_class in mapping["actionClasses"]
        }
        if len(mapped_capabilities) != len(set(mapped_capabilities)):
            violations.append(
                Violation(
                    "CAPABILITY_MAPPING_UNIQUE",
                    "$.capabilityEffects",
                    "each capability may have only one effect mapping",
                )
            )
        if not set(mapped_capabilities) <= capabilities:
            violations.append(
                Violation(
                    "CAPABILITY_MAPPING_UNKNOWN",
                    "$.capabilityEffects",
                    "effect mappings must reference declared capabilities",
                )
            )
        if mapped_effects != effect_classes:
            violations.append(
                Violation(
                    "EFFECT_CLASS_MAPPING_MISMATCH",
                    "$.capabilityEffects",
                    "mapped action classes must exactly equal effectClasses",
                )
            )
    if record_type == "ContextSourceFreshness" and _timestamp(record["freshnessAt"]) > _timestamp(
        record["freshUntil"]
    ):
        violations.append(Violation("FRESHNESS_ORDER", "$.freshUntil", "must follow freshnessAt"))
    if record_type == "EffectDraftPreview":
        window = record["requestedExecutionWindow"]
        if _timestamp(window["notBefore"]) >= _timestamp(window["expiresAt"]):
            violations.append(
                Violation(
                    "DRAFT_WINDOW_ORDER",
                    "$.requestedExecutionWindow",
                    "notBefore must precede expiresAt",
                )
            )
    if record_type == "MetricDefinition" and record["numeratorEvent"] == record["denominatorEvent"]:
        violations.append(
            Violation(
                "RATIO_EVENT_COLLISION",
                "$.denominatorEvent",
                "numerator and denominator events must differ",
            )
        )
    if record_type == "MetricObservation" and _timestamp(record["windowStart"]) >= _timestamp(
        record["windowEnd"]
    ):
        violations.append(
            Violation("METRIC_WINDOW_ORDER", "$.windowEnd", "must follow windowStart")
        )
    if record_type == "ReleaseEvidence":
        if (
            record["outcome"] == "not_applicable"
            and record["applicability"] == "platform_invariant"
        ):
            violations.append(
                Violation(
                    "PLATFORM_GATE_APPLICABILITY",
                    "$.outcome",
                    "platform invariants cannot be not_applicable",
                )
            )
        if _timestamp(record["expiresAt"]) <= current and record["outcome"] == "pass":
            violations.append(
                Violation("RELEASE_EVIDENCE_EXPIRED", "$.expiresAt", "passing evidence is expired")
            )
    if record_type == "AccessibilityEvidence":
        if record["outcome"] == "current" and _timestamp(record["expiresAt"]) <= current:
            violations.append(
                Violation(
                    "ACCESSIBILITY_EVIDENCE_EXPIRED", "$.expiresAt", "current evidence is expired"
                )
            )
        if record["outcome"] == "waived" and not record.get("waiverReason"):
            violations.append(
                Violation(
                    "ACCESSIBILITY_WAIVER_REASON",
                    "$.waiverReason",
                    "waiver requires reason and approval",
                )
            )
        if record["outcome"] == "waived" and not record.get("waiverApproverId"):
            violations.append(
                Violation(
                    "ACCESSIBILITY_WAIVER_APPROVER",
                    "$.waiverApproverId",
                    "waiver requires approver",
                )
            )
        protected = {
            "keyboard_access",
            "focus_visibility",
            "semantic_names",
            "equivalent_wcag_obligations",
        }
        if (
            record["outcome"] == "waived"
            and protected.intersection(record.get("affectedObligations", []))
            and not record.get("legalBasis")
        ):
            violations.append(
                Violation(
                    "ACCESSIBILITY_WAIVER_LEGAL_BASIS",
                    "$.legalBasis",
                    "protected WCAG obligations require explicit legal basis",
                )
            )
    if record_type == "AccessibilityBinding":
        expected = sha256_digest(
            {
                key: record[key]
                for key in (
                    "tenantId",
                    "recordId",
                    "recordVersion",
                    "operatorAcceptanceRecordId",
                    "operatorAcceptanceDigest",
                    "closureEvidenceRecordId",
                    "closureEvidenceDigest",
                    "surface",
                    "buildDigest",
                    "releaseDigest",
                    "expiresAt",
                )
            }
        )
        if record["bindingDigest"] != expected:
            violations.append(
                Violation(
                    "ACCESSIBILITY_BINDING_DIGEST",
                    "$.bindingDigest",
                    "bindingDigest must bind the exact acceptance, evidence, surface, build, and release",
                )
            )
        if _timestamp(record["expiresAt"]) <= current:
            violations.append(
                Violation(
                    "ACCESSIBILITY_BINDING_EXPIRED",
                    "$.expiresAt",
                    "accessibility binding is expired",
                )
            )
    if violations:
        raise ContractViolation(violations)
