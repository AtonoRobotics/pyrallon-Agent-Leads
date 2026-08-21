from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from buyer_ops_contracts.digest import sha256_digest
from buyer_ops_contracts.release_evidence import (
    ReleaseEvidenceEvaluator,
    ReleaseEvidenceRejected,
    evaluate_accessibility_bindings,
    evaluate_accessibility_evidence,
    load_gate_registry,
)

NOW = datetime(2030, 1, 1, 1, tzinfo=UTC)


def _release(gate_id: str, applicability: str, scope: str, registry_digest: str) -> dict[str, Any]:
    record = {
        "schemaVersion": "open-019-024/1.1.0",
        "tenantId": "tenant-1",
        "recordId": f"release-{gate_id}",
        "recordVersion": 1,
        "observedAt": "2030-01-01T00:00:00Z",
        "effectiveFrom": "2030-01-01T00:00:00Z",
        "status": "current",
        "evidenceRefs": [f"run-{gate_id}"],
        "recordType": "ReleaseEvidence",
        "gateId": gate_id,
        "gateRegistryVersion": "1.0.0",
        "gateRegistryDigest": registry_digest,
        "applicability": applicability,
        "scope": scope,
        "releaseDigest": "sha256:" + "a" * 64,
        "testVersion": "suite/1.0.0",
        "outcome": "pass",
        "ownerId": "release-owner",
        "expiresAt": "2030-01-02T00:00:00Z",
    }
    if applicability == "capability":
        record["capabilityId"] = scope
    return record


class _Disablement:
    def proves_disabled(self, capability_id: str, evidence_refs: list[str]) -> bool:
        return capability_id == "transaction_coordination" and evidence_refs == ["flag-readback-1"]


def test_release_evidence_requires_registry_bound_passes_for_gate_and_dependencies() -> None:
    registry, digest = load_gate_registry(Path("PRODUCTION-GATE-REGISTRY.yaml"))
    evaluator = ReleaseEvidenceEvaluator(registry, digest, _Disablement(), tenant_id="tenant-1")
    required = evaluator.required_gate_ids(["GATE-001"])
    evidence = [
        _release(gate_id, registry_gate["class"], registry_gate["scope"], digest)
        for gate_id in required
        for registry_gate in [next(g for g in registry["gates"] if g["id"] == gate_id)]
    ]
    accepted = evaluator.evaluate(
        release_digest="sha256:" + "a" * 64,
        directly_applicable_gate_ids=["GATE-001"],
        evidence=evidence,
        now=NOW,
    )
    assert len(accepted) == len(required)

    evidence[0]["releaseDigest"] = "sha256:" + "b" * 64
    with pytest.raises(ReleaseEvidenceRejected, match="exactly one current"):
        evaluator.evaluate(
            release_digest="sha256:" + "a" * 64,
            directly_applicable_gate_ids=["GATE-001"],
            evidence=evidence,
            now=NOW,
        )


def test_release_evidence_rejects_cross_tenant_and_contaminated_sets() -> None:
    registry, digest = load_gate_registry(Path("PRODUCTION-GATE-REGISTRY.yaml"))
    evaluator = ReleaseEvidenceEvaluator(
        registry,
        digest,
        _Disablement(),
        tenant_id="tenant-1",
    )
    gate = next(gate for gate in registry["gates"] if gate["id"] == "GATE-002")
    tenant_a = _release("GATE-002", gate["class"], gate["scope"], digest)
    tenant_b = copy.deepcopy(tenant_a)
    tenant_b["tenantId"] = "tenant-2"
    tenant_b["recordId"] = "release-GATE-002-tenant-2"

    with pytest.raises(ReleaseEvidenceRejected, match="release evidence tenant mismatch"):
        evaluator.evaluate(
            release_digest="sha256:" + "a" * 64,
            directly_applicable_gate_ids=["GATE-002"],
            evidence=[tenant_b],
            now=NOW,
        )
    with pytest.raises(ReleaseEvidenceRejected, match="release evidence tenant mismatch"):
        evaluator.evaluate(
            release_digest="sha256:" + "a" * 64,
            directly_applicable_gate_ids=["GATE-002"],
            evidence=[tenant_a, tenant_b],
            now=NOW,
        )


def test_not_applicable_requires_capability_gate_and_verified_disablement() -> None:
    registry, digest = load_gate_registry(Path("PRODUCTION-GATE-REGISTRY.yaml"))
    evaluator = ReleaseEvidenceEvaluator(registry, digest, _Disablement(), tenant_id="tenant-1")
    evidence = _release("GATE-009", "capability", "transaction_coordination", digest)
    evidence.update(
        outcome="not_applicable",
        capabilityId="transaction_coordination",
        disabledCapabilityEvidenceRefs=["flag-readback-1"],
    )
    accepted = evaluator.evaluate(
        release_digest="sha256:" + "a" * 64,
        directly_applicable_gate_ids=["GATE-009"],
        evidence=[
            evidence,
            *[
                _release(gate_id, gate["class"], gate["scope"], digest)
                for gate_id in evaluator.required_gate_ids(["GATE-009"])
                if gate_id != "GATE-009"
                for gate in [next(g for g in registry["gates"] if g["id"] == gate_id)]
            ],
        ],
        now=NOW,
    )
    assert evidence["recordId"] in accepted


def _accessibility(surface: str, build_digest: str) -> dict[str, Any]:
    return {
        "schemaVersion": "open-019-024/1.1.0",
        "tenantId": "tenant-1",
        "recordId": f"a11y-{surface}",
        "recordVersion": 1,
        "observedAt": "2030-01-01T00:00:00Z",
        "effectiveFrom": "2030-01-01T00:00:00Z",
        "status": "current",
        "evidenceRefs": [f"wcag-run-{surface}"],
        "recordType": "AccessibilityEvidence",
        "standard": "WCAG 2.2 AA",
        "suiteVersion": "a11y-suite/4",
        "surface": surface,
        "buildDigest": build_digest,
        "releaseDigest": "sha256:" + "a" * 64,
        "assistiveTechnologies": ["voiceover", "keyboard"],
        "knownExceptions": [],
        "outcome": "current",
        "ownerId": "accessibility-owner",
        "expiresAt": "2030-01-02T00:00:00Z",
    }


def test_accessibility_acceptance_is_exactly_build_and_release_bound() -> None:
    web = _accessibility("web", "sha256:" + "b" * 64)
    ios = _accessibility("ios", "sha256:" + "c" * 64)
    assert evaluate_accessibility_evidence(
        [web, ios],
        tenant_id="tenant-1",
        release_digest="sha256:" + "a" * 64,
        deployed_builds={"web": web["buildDigest"], "ios": ios["buildDigest"]},
        now=NOW,
    ) == ("a11y-ios", "a11y-web")

    foreign = copy.deepcopy(ios)
    foreign["tenantId"] = "tenant-2"
    with pytest.raises(ReleaseEvidenceRejected, match="accessibility evidence tenant mismatch"):
        evaluate_accessibility_evidence(
            [web, foreign],
            tenant_id="tenant-1",
            release_digest="sha256:" + "a" * 64,
            deployed_builds={"web": web["buildDigest"], "ios": ios["buildDigest"]},
            now=NOW,
        )
    wrong = copy.deepcopy(web)
    wrong["buildDigest"] = "sha256:" + "d" * 64
    with pytest.raises(ReleaseEvidenceRejected, match="current build-bound"):
        evaluate_accessibility_evidence(
            [wrong, ios],
            tenant_id="tenant-1",
            release_digest="sha256:" + "a" * 64,
            deployed_builds={"web": web["buildDigest"], "ios": ios["buildDigest"]},
            now=NOW,
        )


def test_accessibility_binding_binds_acceptance_evidence_surface_and_build() -> None:
    web = _accessibility("web", "sha256:" + "b" * 64)
    acceptance_digest = "sha256:" + "c" * 64
    binding = {
        "schemaVersion": "open-019-024/1.1.0",
        "tenantId": "tenant-1",
        "recordId": "binding-web",
        "recordVersion": 1,
        "observedAt": "2030-01-01T00:00:00Z",
        "effectiveFrom": "2030-01-01T00:00:00Z",
        "status": "current",
        "evidenceRefs": ["acceptance-web", web["recordId"]],
        "recordType": "AccessibilityBinding",
        "operatorAcceptanceRecordId": "acceptance-web",
        "operatorAcceptanceDigest": acceptance_digest,
        "closureEvidenceRecordId": web["recordId"],
        "closureEvidenceDigest": sha256_digest(web),
        "surface": "web",
        "buildDigest": web["buildDigest"],
        "releaseDigest": web["releaseDigest"],
        "expiresAt": "2030-01-02T00:00:00Z",
    }
    binding["bindingDigest"] = sha256_digest(
        {
            key: binding[key]
            for key in (
                "tenantId",
                "recordId",
                "recordVersion",
                "operatorAcceptanceRecordId",
                "operatorAcceptanceDigest",
                "closureEvidenceRecordId",
                "closureEvidenceDigest",
                "surface",
                "buildDigest",
                "releaseDigest",
                "expiresAt",
            )
        }
    )
    assert evaluate_accessibility_bindings(
        [binding],
        [web],
        tenant_id="tenant-1",
        release_digest=web["releaseDigest"],
        deployed_builds={"web": web["buildDigest"]},
        acceptance_digests={"web": acceptance_digest},
        now=NOW,
    ) == ("binding-web",)

    drifted = copy.deepcopy(binding)
    drifted["operatorAcceptanceDigest"] = "sha256:" + "d" * 64
    drifted["bindingDigest"] = sha256_digest(
        {
            key: drifted[key]
            for key in (
                "tenantId",
                "recordId",
                "recordVersion",
                "operatorAcceptanceRecordId",
                "operatorAcceptanceDigest",
                "closureEvidenceRecordId",
                "closureEvidenceDigest",
                "surface",
                "buildDigest",
                "releaseDigest",
                "expiresAt",
            )
        }
    )
    with pytest.raises(ReleaseEvidenceRejected, match="acceptance digest mismatch"):
        evaluate_accessibility_bindings(
            [drifted],
            [web],
            tenant_id="tenant-1",
            release_digest=web["releaseDigest"],
            deployed_builds={"web": web["buildDigest"]},
            acceptance_digests={"web": acceptance_digest},
            now=NOW,
        )
