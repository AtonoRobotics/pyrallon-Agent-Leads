"""GATE-007 mechanical fair-housing controls.

Protected-class tokens are matched as whole tokens, never as substrings, so
ordinary operational language (Sussex, paint color) is not treated as a trait.
Undeclared features are unavailable. A counterfactual pair that changes a
decision is a release-blocking difference.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, NoReturn

from .errors import ContractViolation, Violation

ALLOWED_CRITERIA = frozenset(
    {
        "identity",
        "representation",
        "purchase_intent",
        "geography",
        "property",
        "timing",
        "budget_financing",
        "contingency",
        "decision_participants",
        "scheduling",
        "channel",
    }
)

PROHIBITED_TOKENS = frozenset(
    {
        "race",
        "color",
        "religion",
        "sex",
        "gender",
        "national_origin",
        "disability",
        "familial_status",
        "familial",
        "children",
        "pregnant",
        "steering",
    }
)

_TOKEN = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")


def tokenize(text: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(text.lower().replace("-", "_")))


def _prohibited_in(text: str) -> frozenset[str]:
    tokens = tokenize(text)
    hits = set(tokens & PROHIBITED_TOKENS)
    if "national" in tokens and "origin" in tokens:
        hits.add("national_origin")
    if "familial" in tokens and "status" in tokens:
        hits.add("familial_status")
    return frozenset(hits)


def assert_allowed_criterion(criterion_id: str) -> None:
    hits = _prohibited_in(criterion_id)
    if hits:
        raise ContractViolation(
            [
                Violation(
                    "PROHIBITED_PROXY",
                    "$.criterionId",
                    "protected trait or prohibited proxy cannot be a qualification feature",
                )
            ]
        )
    if criterion_id not in ALLOWED_CRITERIA:
        raise ContractViolation(
            [
                Violation(
                    "CRITERION_NOT_ALLOWLISTED",
                    "$.criterionId",
                    f"{criterion_id} is not an allowed operational feature",
                )
            ]
        )


def assert_no_protected_influence(text: str) -> None:
    if _prohibited_in(text):
        raise ContractViolation(
            [
                Violation(
                    "PROHIBITED_PROXY",
                    "$.text",
                    "protected trait language cannot influence service, cadence, or ranking",
                )
            ]
        )


@dataclass(frozen=True, slots=True)
class CompiledFeatures:
    criterion_id: str
    tokens: frozenset[str]


def compile_features(criterion_id: str, text: str) -> CompiledFeatures:
    """Admit only allowlisted operational features after token-level proxy rejection."""
    assert_allowed_criterion(criterion_id)
    assert_no_protected_influence(text)
    return CompiledFeatures(criterion_id=criterion_id, tokens=tokenize(text) - PROHIBITED_TOKENS)


@dataclass(frozen=True, slots=True)
class CounterfactualResult:
    pairs_evaluated: int
    decision_differences: int

    @property
    def difference_ratio(self) -> float | None:
        if self.pairs_evaluated == 0:
            return None
        return self.decision_differences / self.pairs_evaluated


def evaluate_counterfactuals(
    criterion_id: str,
    text: str,
    *,
    decide: Any | None = None,
) -> CounterfactualResult:
    """Perturb protected tokens. A decision change is a counted difference.

    The default decision is the compiled allowlisted feature identity. Insertion of
    a prohibited token must fail closed (PROHIBITED_PROXY) or leave that identity
    unchanged. A successful compile that yields different features is a difference.
    """
    try:
        baseline = compile_features(criterion_id, text)
    except ContractViolation:
        return CounterfactualResult(pairs_evaluated=0, decision_differences=0)

    decide_fn = decide or (lambda compiled: compiled.criterion_id)
    base_decision = decide_fn(baseline)
    differences = 0
    evaluated = 0
    for token in sorted(PROHIBITED_TOKENS):
        perturbed = f"{text} {token}"
        evaluated += 1
        try:
            compiled = compile_features(criterion_id, perturbed)
        except ContractViolation:
            continue
        if decide_fn(compiled) != base_decision:
            differences += 1
    return CounterfactualResult(pairs_evaluated=evaluated, decision_differences=differences)


def normalize_governing_text(value: str) -> str:
    """Apply the exact OPEN-027 normalization pipeline."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _unicode_words(value: str) -> tuple[str, ...]:
    normalized = normalize_governing_text(value)
    words: list[str] = []
    current: list[str] = []
    for character in normalized:
        category = unicodedata.category(character)
        if category[0] in {"L", "N"} or category in {"Mn", "Mc", "Pc"}:
            current.append(character)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return tuple(words)


@dataclass(frozen=True, slots=True)
class AdmittedFeature:
    feature_id: str
    value: Any
    source_class: str
    purpose: str
    action_class: str


class FairHousingCompiler:
    """Profile-driven OPEN-027 compiler with no built-in policy defaults."""

    def __init__(self, profile: dict[str, Any]) -> None:
        if profile.get("normalization") != "unicode_nfkc_casefold_whitespace_v1":
            self._deny("NORMALIZATION_UNSUPPORTED", "$.normalization", "unsupported algorithm")
        if profile.get("matchMode") != "unicode_token_and_phrase_boundary":
            self._deny("MATCH_MODE_UNSUPPORTED", "$.matchMode", "unsupported match mode")
        self.profile = profile
        self._features = {item["featureId"]: item for item in profile["allowedFeatures"]}

    @staticmethod
    def _deny(code: str, path: str, message: str) -> NoReturn:
        raise ContractViolation([Violation(code, path, message)])

    def protected_matches(self, text: str) -> set[str]:
        words = _unicode_words(text)
        matches: set[str] = set()
        token_set = set(words)
        for declared in self.profile["protectedTokens"]:
            normalized = normalize_governing_text(declared)
            if len(_unicode_words(normalized)) == 1 and normalized in token_set:
                matches.add(declared)
        for declared in self.profile["protectedPhrases"]:
            phrase = _unicode_words(declared)
            if phrase and any(
                words[index : index + len(phrase)] == phrase
                for index in range(len(words) - len(phrase) + 1)
            ):
                matches.add(declared)
        return matches

    def compile_feature(
        self,
        feature_id: str,
        *,
        value: Any,
        source_class: str,
        purpose: str,
        action_class: str,
    ) -> AdmittedFeature:
        rule = self._features.get(feature_id)
        if rule is None:
            self._deny(
                "FEATURE_NOT_ALLOWLISTED", "$.featureId", "undeclared feature fails closed"
            )
        if source_class != rule["sourceClass"]:
            self._deny("FEATURE_SOURCE_DENIED", "$.sourceClass", "source class is not declared")
        if purpose not in rule["allowedPurposes"]:
            self._deny("FEATURE_PURPOSE_DENIED", "$.purpose", "purpose is not declared")
        if action_class not in rule["allowedActionClasses"]:
            self._deny(
                "FEATURE_ACTION_DENIED", "$.actionClass", "action class is not declared"
            )
        if isinstance(value, str) and self.protected_matches(value):
            self._deny(
                "PROTECTED_TRAIT_MATCH",
                "$.value",
                "protected trait language cannot influence the action",
            )
        return AdmittedFeature(feature_id, value, source_class, purpose, action_class)

    def assert_optimizer_output(
        self, *, inputs: dict[str, Any], output: dict[str, Any]
    ) -> None:
        immutable = list(self.profile["immutableServiceGuarantees"]) + list(
            self.profile["optimizerBounds"]
        )
        for field in immutable:
            if field not in inputs or output.get(field) != inputs[field]:
                self._deny(
                    "IMMUTABLE_OPTIMIZER_INPUT",
                    f"$.{field}",
                    "optimizer changed or omitted a governed immutable input",
                )


def assert_counterfactual_invariance(
    baseline: dict[str, Any],
    counterfactual: dict[str, Any],
    *,
    invariant_fields: list[str],
) -> None:
    for field in invariant_fields:
        if field not in baseline or field not in counterfactual or baseline[field] != counterfactual[field]:
            raise ContractViolation(
                [
                    Violation(
                        "COUNTERFACTUAL_MISMATCH",
                        f"$.{field}",
                        "governed outcome changed under protected-trait counterfactual",
                    )
                ]
            )


def validate_promotion_evidence(
    profile: dict[str, Any],
    *,
    metric_id: str,
    sample_size: int,
    metric_value: float,
    confidence_method: str,
) -> None:
    criteria = [item for item in profile["promotionCriteria"] if item["metricId"] == metric_id]
    if len(criteria) != 1:
        raise ContractViolation(
            [Violation("PROMOTION_CRITERION_MISSING", "$.metricId", "no unique criterion")]
        )
    criterion = criteria[0]
    if sample_size < criterion["minimumSample"]:
        raise ContractViolation(
            [Violation("PROMOTION_SAMPLE_TOO_SMALL", "$.sampleSize", "minimum not met")]
        )
    if confidence_method != criterion["confidenceMethod"]:
        raise ContractViolation(
            [Violation("PROMOTION_CONFIDENCE_MISMATCH", "$.confidenceMethod", "method differs")]
        )
    if metric_value < criterion["parityThreshold"]:
        raise ContractViolation(
            [Violation("PROMOTION_PARITY_FAILED", "$.metricValue", "threshold not met")]
        )
