"""Deterministic, provider-independent production evaluation runner.

The runner scores externally produced cognitive proposals against immutable
golden cases. It never treats a model's own claim of success as evidence: each
case has explicit safety, authority, and state assertions, and the suite fails
closed below its configured threshold.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class EvaluationConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    case_id: str
    action_class: str
    input: dict[str, Any]
    expected: dict[str, Any]

    @classmethod
    def from_value(cls, value: Any) -> EvaluationCase:
        if not isinstance(value, dict):
            raise EvaluationConfigurationError("evaluation case must be an object")
        required = {"caseId", "actionClass", "input", "expected"}
        if set(value) != required or any(
            not isinstance(value.get(key), str) or not value[key]
            for key in ("caseId", "actionClass")
        ):
            raise EvaluationConfigurationError("evaluation case has an invalid identity")
        if not isinstance(value["input"], dict) or not isinstance(value["expected"], dict):
            raise EvaluationConfigurationError("evaluation case input and expected must be objects")
        return cls(
            str(value["caseId"]), str(value["actionClass"]), value["input"], value["expected"]
        )


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    case_id: str
    passed: bool
    failures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    suite_version: str
    results: tuple[EvaluationResult, ...]
    score: float
    passed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": "buyer-ops-evaluation-report/1.0.0",
            "suiteVersion": self.suite_version,
            "score": self.score,
            "passed": self.passed,
            "results": [
                {
                    "caseId": result.case_id,
                    "passed": result.passed,
                    "failures": list(result.failures),
                }
                for result in self.results
            ],
        }


class EvaluationSuite:
    def __init__(
        self, suite_version: str, cases: tuple[EvaluationCase, ...], *, minimum_score: float = 1.0
    ) -> None:
        if not suite_version or not cases:
            raise EvaluationConfigurationError("evaluation suite requires a version and cases")
        if not 0 <= minimum_score <= 1:
            raise EvaluationConfigurationError(
                "minimum evaluation score must be between zero and one"
            )
        if len({case.case_id for case in cases}) != len(cases):
            raise EvaluationConfigurationError("evaluation case IDs must be unique")
        self.suite_version = suite_version
        self.cases = cases
        self.minimum_score = minimum_score

    @classmethod
    def from_path(cls, path: Path) -> EvaluationSuite:
        document = json.loads(path.read_text())
        if not isinstance(document, dict) or not isinstance(document.get("suiteVersion"), str):
            raise EvaluationConfigurationError("evaluation suite root is invalid")
        raw_cases = document.get("cases")
        if not isinstance(raw_cases, list):
            raise EvaluationConfigurationError("evaluation suite cases must be an array")
        return cls(
            str(document["suiteVersion"]),
            tuple(EvaluationCase.from_value(value) for value in raw_cases),
            minimum_score=float(document.get("minimumScore", 1.0)),
        )

    def run(self, candidate: Callable[[EvaluationCase], dict[str, Any]]) -> EvaluationReport:
        results: list[EvaluationResult] = []
        for case in self.cases:
            try:
                actual = candidate(case)
                if not isinstance(actual, dict):
                    raise ValueError("candidate result must be an object")
                failures = tuple(_assert_expected(case.expected, actual, path="$"))
            except Exception as exc:
                failures = (f"candidate_error:{type(exc).__name__}:{exc}",)
            results.append(EvaluationResult(case.case_id, not failures, failures))
        score = sum(result.passed for result in results) / len(results)
        return EvaluationReport(
            self.suite_version, tuple(results), score, score >= self.minimum_score
        )


def _assert_expected(expected: dict[str, Any], actual: dict[str, Any], *, path: str) -> list[str]:
    failures: list[str] = []
    for key, wanted in expected.items():
        current = actual.get(key)
        if isinstance(wanted, dict):
            if not isinstance(current, dict):
                failures.append(f"{path}.{key}:expected_object")
            else:
                failures.extend(_assert_expected(wanted, current, path=f"{path}.{key}"))
        elif isinstance(wanted, list):
            if current != wanted:
                failures.append(f"{path}.{key}:expected={wanted!r}:actual={current!r}")
        elif current != wanted:
            failures.append(f"{path}.{key}:expected={wanted!r}:actual={current!r}")
    return failures
