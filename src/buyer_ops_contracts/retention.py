"""Fail-closed record-class retention configuration for FR-25 and OPEN-009."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


class RetentionConfigurationIncomplete(ValueError):
    """Raised when deployment-owned retention choices have not been supplied."""


# TREC Rule 535.2(h): at least four years from closing or contract termination.
NON_CONFIGURABLE_LEGAL_FLOOR_YEARS: Mapping[str, int] = {
    "trec_covered_brokerage_record": 4,
}


@dataclass(frozen=True, slots=True)
class RetentionConfiguration:
    policy_version: str
    owner_ref: str
    effective_at: datetime
    deletion_completion_slo: timedelta
    period_years_by_record_class: Mapping[str, int]
    object_lock_record_classes: frozenset[str]

    def validate_for(self, required_record_classes: frozenset[str]) -> None:
        if not self.policy_version or not self.owner_ref:
            raise RetentionConfigurationIncomplete(
                "retention policy version and owner are required"
            )
        if self.effective_at.tzinfo is None:
            raise RetentionConfigurationIncomplete("retention effective_at must include an offset")
        if self.deletion_completion_slo <= timedelta(0):
            raise RetentionConfigurationIncomplete("deletion completion SLO must be positive")
        missing = required_record_classes - self.period_years_by_record_class.keys()
        if missing:
            raise RetentionConfigurationIncomplete(
                f"retention periods are missing for enabled classes: {sorted(missing)}"
            )
        for record_class, years in self.period_years_by_record_class.items():
            if years <= 0:
                raise RetentionConfigurationIncomplete(
                    f"retention period must be positive: {record_class}"
                )
            floor = NON_CONFIGURABLE_LEGAL_FLOOR_YEARS.get(record_class)
            if floor is not None and years < floor:
                raise RetentionConfigurationIncomplete(
                    f"{record_class} retention must be at least {floor} years"
                )
        unknown_locks = self.object_lock_record_classes - self.period_years_by_record_class.keys()
        if unknown_locks:
            raise RetentionConfigurationIncomplete(
                f"object lock classes lack retention configuration: {sorted(unknown_locks)}"
            )


class RetentionPolicy:
    def __init__(
        self,
        configuration: RetentionConfiguration,
        *,
        required_record_classes: frozenset[str],
    ) -> None:
        configuration.validate_for(required_record_classes)
        self.configuration = configuration

    def retain_until(self, *, record_class: str, starts_at: datetime) -> datetime:
        try:
            years = self.configuration.period_years_by_record_class[record_class]
        except KeyError as exc:
            raise RetentionConfigurationIncomplete(
                f"retention is not configured for record class: {record_class}"
            ) from exc
        normalized = starts_at.astimezone(UTC)
        try:
            return normalized.replace(year=normalized.year + years)
        except ValueError:
            # Calendar-year retention from February 29 ends on February 28.
            return normalized.replace(year=normalized.year + years, day=28)

    def deletion_eligible(
        self,
        *,
        retain_until: datetime,
        active_legal_hold_ids: frozenset[str],
        now: datetime,
    ) -> bool:
        if active_legal_hold_ids:
            return False
        return now.astimezone(UTC) >= retain_until.astimezone(UTC)

    def requires_object_lock(self, *, record_class: str, legal_hold_active: bool) -> bool:
        return legal_hold_active or record_class in self.configuration.object_lock_record_classes
