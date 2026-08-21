from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest

from buyer_ops_contracts.errors import ContractViolation
from buyer_ops_contracts.semantic import SemanticPolicy, validate_semantics


def test_cognitive_proposal_is_admitted_with_the_published_ttl_policy(load_fixture) -> None:
    proposal = load_fixture("valid/cognitive_proposal.json")
    validate_semantics(
        proposal,
        SemanticPolicy(
            now=datetime(2029, 12, 31, tzinfo=UTC),
            max_proposal_ttl={"lead_qualification_draft": timedelta(minutes=15)},
        ),
    )


def test_cognitive_proposal_rejects_a_claim_fresher_than_generation(load_fixture) -> None:
    proposal = copy.deepcopy(load_fixture("valid/cognitive_proposal.json"))
    proposal["claims"][0]["freshnessAt"] = "2030-01-01T10:02:00Z"
    with pytest.raises(ContractViolation):
        validate_semantics(
            proposal,
            SemanticPolicy(now=datetime(2029, 12, 31, tzinfo=UTC)),
        )
