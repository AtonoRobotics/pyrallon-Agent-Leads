from __future__ import annotations

import pytest

from buyer_ops_contracts.errors import ContractViolation
from buyer_ops_contracts.fair_housing import (
    FairHousingCompiler,
    assert_counterfactual_invariance,
    validate_promotion_evidence,
)


def _profile() -> dict:
    return {
        "profileVersion": "fh-1",
        "normalization": "unicode_nfkc_casefold_whitespace_v1",
        "matchMode": "unicode_token_and_phrase_boundary",
        "protectedTokens": ["sex", "religion"],
        "protectedPhrases": ["national origin"],
        "allowedFeatures": [
            {
                "featureId": "appointment_window",
                "sourceClass": "buyer_stated",
                "allowedPurposes": ["schedule_showing"],
                "allowedActionClasses": ["scheduling"],
            }
        ],
        "prohibitedProxyRules": ["protected_term"],
        "immutableServiceGuarantees": ["response_slo", "quiet_hours"],
        "optimizerBounds": ["maximum_frequency"],
        "promotionCriteria": [
            {
                "metricId": "service_parity",
                "sliceDefinition": "declared protected slices",
                "minimumSample": 100,
                "parityThreshold": 0.8,
                "confidenceMethod": "bootstrap_95",
                "rollbackTrigger": "parity_below_threshold",
            }
        ],
    }


def test_unicode_normalization_phrase_boundaries_and_sussex_negative() -> None:
    compiler = FairHousingCompiler(_profile())
    assert compiler.protected_matches("ＮＡＴＩＯＮＡＬ   Origin") == {"national origin"}
    assert compiler.protected_matches("Sussex appointments") == set()
    assert compiler.protected_matches("sex") == {"sex"}


def test_feature_is_bound_to_declared_purpose_and_action() -> None:
    compiler = FairHousingCompiler(_profile())
    admitted = compiler.compile_feature(
        "appointment_window",
        value="Saturday morning",
        source_class="buyer_stated",
        purpose="schedule_showing",
        action_class="scheduling",
    )
    assert admitted.feature_id == "appointment_window"
    with pytest.raises(ContractViolation, match="FEATURE_PURPOSE_DENIED"):
        compiler.compile_feature(
            "appointment_window",
            value="Saturday morning",
            source_class="buyer_stated",
            purpose="lead_ranking",
            action_class="scheduling",
        )


def test_undeclared_and_protected_free_text_fail_closed() -> None:
    compiler = FairHousingCompiler(_profile())
    with pytest.raises(ContractViolation, match="FEATURE_NOT_ALLOWLISTED"):
        compiler.compile_feature(
            "zip_code",
            value="90210",
            source_class="buyer_stated",
            purpose="schedule_showing",
            action_class="scheduling",
        )
    with pytest.raises(ContractViolation, match="PROTECTED_TRAIT_MATCH"):
        compiler.compile_feature(
            "appointment_window",
            value="religion preference",
            source_class="buyer_stated",
            purpose="schedule_showing",
            action_class="scheduling",
        )


def test_service_guarantees_and_counterfactual_outcomes_are_immutable() -> None:
    compiler = FairHousingCompiler(_profile())
    with pytest.raises(ContractViolation, match="IMMUTABLE_OPTIMIZER_INPUT"):
        compiler.assert_optimizer_output(
            inputs={"response_slo": "5m", "quiet_hours": "22:00-08:00", "maximum_frequency": 3},
            output={"response_slo": "10m", "quiet_hours": "22:00-08:00", "maximum_frequency": 3},
        )
    assert_counterfactual_invariance(
        {"eligibility": True, "rank": 2},
        {"eligibility": True, "rank": 2},
        invariant_fields=["eligibility", "rank"],
    )
    with pytest.raises(ContractViolation, match="COUNTERFACTUAL_MISMATCH"):
        assert_counterfactual_invariance(
            {"eligibility": True}, {"eligibility": False}, invariant_fields=["eligibility"]
        )


def test_promotion_uses_declared_criteria_without_code_defaults() -> None:
    validate_promotion_evidence(
        _profile(),
        metric_id="service_parity",
        sample_size=100,
        metric_value=0.81,
        confidence_method="bootstrap_95",
    )
    with pytest.raises(ContractViolation, match="PROMOTION_SAMPLE_TOO_SMALL"):
        validate_promotion_evidence(
            _profile(),
            metric_id="service_parity",
            sample_size=99,
            metric_value=0.99,
            confidence_method="bootstrap_95",
        )
    with pytest.raises(ContractViolation, match="PROMOTION_CRITERION_MISSING"):
        validate_promotion_evidence(
            {**_profile(), "promotionCriteria": []},
            metric_id="service_parity",
            sample_size=100,
            metric_value=1.0,
            confidence_method="bootstrap_95",
        )
