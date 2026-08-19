from datetime import UTC, datetime
from typing import Any

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
    if violations:
        raise ContractViolation(violations)

