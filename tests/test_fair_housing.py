from buyer_ops_contracts.errors import ContractViolation
from buyer_ops_contracts.fair_housing import (
    compile_features,
    evaluate_counterfactuals,
    tokenize,
)


def test_sussex_is_not_a_protected_sex_token() -> None:
    compiled = compile_features("geography", "Buyer asked about Sussex listings")
    assert compiled.criterion_id == "geography"
    assert "sussex" in compiled.tokens
    assert "sex" not in tokenize("Sussex")


def test_children_token_is_rejected() -> None:
    try:
        compile_features("timing", "they have children in the district")
    except ContractViolation as exc:
        assert any(item.code == "PROHIBITED_PROXY" for item in exc.violations)
    else:
        raise AssertionError("expected PROHIBITED_PROXY")


def test_undeclared_criterion_is_unavailable() -> None:
    try:
        compile_features("credit_score", "stated 740")
    except ContractViolation as exc:
        assert any(item.code == "CRITERION_NOT_ALLOWLISTED" for item in exc.violations)
    else:
        raise AssertionError("expected CRITERION_NOT_ALLOWLISTED")


def test_protected_token_injection_does_not_change_allowlisted_decision() -> None:
    result = evaluate_counterfactuals("geography", "San Antonio east side")
    assert result.pairs_evaluated == 12
    assert result.decision_differences == 0
    assert result.difference_ratio == 0
