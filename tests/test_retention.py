from datetime import UTC, datetime, timedelta

import pytest

from buyer_ops_contracts.retention import (
    RetentionConfiguration,
    RetentionConfigurationIncomplete,
    RetentionPolicy,
)


def _configuration(**overrides):
    values = {
        "policy_version": "retention/1",
        "owner_ref": "broker-policy-owner-1",
        "effective_at": datetime(2029, 1, 1, tzinfo=UTC),
        "deletion_completion_slo": timedelta(hours=24),
        "period_years_by_record_class": {
            "trec_covered_brokerage_record": 4,
            "buyer_communication": 5,
        },
        "object_lock_record_classes": frozenset({"trec_covered_brokerage_record"}),
    }
    values.update(overrides)
    return RetentionConfiguration(**values)


def test_retention_requires_every_enabled_class_and_legal_floor() -> None:
    with pytest.raises(RetentionConfigurationIncomplete, match="missing"):
        RetentionPolicy(
            _configuration(),
            required_record_classes=frozenset({"trec_covered_brokerage_record", "approval"}),
        )
    with pytest.raises(RetentionConfigurationIncomplete, match="at least 4 years"):
        RetentionPolicy(
            _configuration(period_years_by_record_class={"trec_covered_brokerage_record": 3}),
            required_record_classes=frozenset({"trec_covered_brokerage_record"}),
        )


def test_calendar_year_retention_and_legal_hold_are_fail_closed() -> None:
    policy = RetentionPolicy(
        _configuration(),
        required_record_classes=frozenset({"trec_covered_brokerage_record", "buyer_communication"}),
    )
    retained = policy.retain_until(
        record_class="trec_covered_brokerage_record",
        starts_at=datetime(2028, 2, 29, 12, tzinfo=UTC),
    )
    assert retained == datetime(2032, 2, 29, 12, tzinfo=UTC)
    assert not policy.deletion_eligible(
        retain_until=retained,
        active_legal_hold_ids=frozenset({"hold-1"}),
        now=datetime(2033, 1, 1, tzinfo=UTC),
    )
    assert policy.deletion_eligible(
        retain_until=retained,
        active_legal_hold_ids=frozenset(),
        now=retained,
    )
    assert policy.requires_object_lock(record_class="buyer_communication", legal_hold_active=True)
