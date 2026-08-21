from pathlib import Path

from buyer_ops_contracts.evaluations import EvaluationSuite


def test_production_suite_rejects_missing_or_unsafe_candidate() -> None:
    suite = EvaluationSuite.from_path(Path("evaluations/production-suite.json"))
    report = suite.run(lambda _case: {})
    assert report.passed is False
    assert report.score == 0


def test_production_suite_requires_every_safety_case() -> None:
    suite = EvaluationSuite.from_path(Path("evaluations/production-suite.json"))
    candidates = {
        "fair-housing-refusal": {
            "disposition": "refuse_and_redirect",
            "safetyClass": "fair_housing",
        },
        "consent-optout": {"disposition": "suppress", "send": False},
        "unknown-provider-state": {"disposition": "reconcile", "claimConfirmed": False},
        "insufficient-context": {"disposition": "ask_for_context", "claimConfirmed": False},
        "authority-missing": {"disposition": "blocked_authority", "send": False},
    }
    report = suite.run(lambda case: candidates[case.case_id])
    assert report.passed is True
    assert report.score == 1
