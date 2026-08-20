from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from buyer_ops_contracts.acknowledgment import (
    build_acknowledgment_decision,
    normalize_opt_out_text,
    validate_acknowledgment_config,
)

ROOT = Path(__file__).resolve().parents[1]


def _valid(name: str) -> dict:
    records = json.loads((ROOT / "tests/fixtures/closure/ot01_ingress_valid.json").read_text())
    return copy.deepcopy(records[name])


def _suppression() -> dict:
    records = json.loads((ROOT / "tests/fixtures/generated/ontology_0_3_valid.json").read_text())
    record = copy.deepcopy(records["Suppression"])
    record.update(
        id="suppression-1",
        tenantId="tenantId-1",
        subjectId="person-1",
        endpointId="recipient-1",
        scope="channel_all",
        reason="opt_out",
        suppressedAt="2030-01-01T00:00:01Z",
        validityState="active",
        status="active",
        sourceEvidenceIds=["evidence-1"],
    )
    return record


def test_normalization_and_match_modes_are_exact() -> None:
    assert normalize_opt_out_text("  ＳＴＯＰ\t now  ") == "stop now"
    policy, lexicon, request = (
        _valid("AcknowledgmentPolicy"),
        _valid("OptOutLexicon"),
        _valid("AcknowledgmentDecisionRequest"),
    )
    template = b"Hello {{first_name}}"
    policy["rules"][0]["templateDigest"] = f"sha256:{hashlib.sha256(template).hexdigest()}"

    assert (
        build_acknowledgment_decision(request, policy, lexicon, "stop now", template)[
            "optOutMatched"
        ]
        is False
    )
    lexicon["matchMode"] = "leading_token"
    request["suppressionRecordCandidate"] = _suppression()
    decision = build_acknowledgment_decision(request, policy, lexicon, "stop now", template)
    assert decision["optOutMatched"] is True
    assert decision["disposition"] == "suppress_and_acknowledge"


def test_policy_selection_order_must_cover_each_rule_once() -> None:
    policy = _valid("AcknowledgmentPolicy")
    policy["selectionOrder"] = ["missing-rule"]
    with pytest.raises(ValueError, match="selectionOrder"):
        validate_acknowledgment_config(policy)


def test_configuration_lifecycle_and_temporal_interval_are_closed() -> None:
    policy = _valid("AcknowledgmentPolicy")
    policy.update(recordVersion=2, supersedesRecordId="another-policy")
    with pytest.raises(ValueError, match="predecessor"):
        validate_acknowledgment_config(policy)
    policy.update(supersedesRecordId="policy-1", effectiveTo=policy["effectiveFrom"])
    with pytest.raises(ValueError, match="effectiveTo"):
        validate_acknowledgment_config(policy)


def test_lexicon_channel_or_locale_mismatch_fails_closed() -> None:
    policy, lexicon, request = (
        _valid("AcknowledgmentPolicy"),
        _valid("OptOutLexicon"),
        _valid("AcknowledgmentDecisionRequest"),
    )
    lexicon["channels"] = ["email"]
    decision = build_acknowledgment_decision(request, policy, lexicon, "stop", b"")
    assert decision["disposition"] == "configuration_incomplete"
    assert decision["externalMessageIdentityRef"] == request["externalMessageIdentityRef"]


def test_template_digest_and_substitution_set_are_closed() -> None:
    policy, lexicon, request = (
        _valid("AcknowledgmentPolicy"),
        _valid("OptOutLexicon"),
        _valid("AcknowledgmentDecisionRequest"),
    )
    with pytest.raises(ValueError, match="template digest"):
        build_acknowledgment_decision(request, policy, lexicon, "hello", b"Hello {{first_name}}")

    template = b"Hello {{first_name}}"
    policy["rules"][0]["templateDigest"] = f"sha256:{hashlib.sha256(template).hexdigest()}"
    request["substitutions"]["undeclared"] = "value"
    with pytest.raises(ValueError, match="substitution"):
        build_acknowledgment_decision(request, policy, lexicon, "hello", template)


def test_opt_out_requires_complete_bound_suppression_candidate() -> None:
    policy, lexicon, request = (
        _valid("AcknowledgmentPolicy"),
        _valid("OptOutLexicon"),
        _valid("AcknowledgmentDecisionRequest"),
    )
    template = b"Hello {{first_name}}"
    policy["rules"][0]["templateDigest"] = f"sha256:{hashlib.sha256(template).hexdigest()}"
    with pytest.raises(ValueError, match="suppressionRecordCandidate"):
        build_acknowledgment_decision(request, policy, lexicon, "stop", template)
