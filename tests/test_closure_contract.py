import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from buyer_ops_contracts import ContractViolation, validate_closure_semantics


def _schema() -> dict:
    return json.loads(Path("OPEN-019-024.schema.json").read_text())


def _base(record_type: str) -> dict:
    return {
        "schemaVersion": "open-019-024/1.1.0",
        "tenantId": "tenant-1",
        "recordId": record_type.lower(),
        "recordVersion": 1,
        "observedAt": "2030-01-01T00:00:00Z",
        "effectiveFrom": "2030-01-01T00:00:00Z",
        "status": "current",
        "evidenceRefs": ["evidence-1"],
        "recordType": record_type,
    }


def test_external_message_identity_is_stable_and_structural() -> None:
    record = _base("ExternalMessageIdentity") | {
        "connectorId": "gmail-primary",
        "provider": "gmail",
        "providerAccountRef": "account-1",
        "externalMessageId": "message-1",
        "externalEventId": "event-1",
        "payloadDigest": "sha256:" + "a" * 64,
    }
    assert not list(Draft202012Validator(_schema()).iter_errors(record))


def test_context_freshness_and_ratio_rules_fail_closed() -> None:
    freshness = _base("ContextSourceFreshness") | {
        "sourceRecordId": "evidence-1",
        "epistemicType": "verified_fact",
        "freshnessAt": "2030-01-02T00:00:00Z",
        "freshUntil": "2030-01-01T00:00:00Z",
    }
    with pytest.raises(ContractViolation, match="FRESHNESS_ORDER"):
        validate_closure_semantics(freshness)
    metric = _base("MetricDefinition") | {
        "metricId": "consult-rate",
        "unit": "ratio",
        "numeratorEvent": "consult.booked",
        "denominatorEvent": "consult.booked",
        "correlationKey": "journeyId",
        "dimensions": [],
        "window": "7d",
        "minimumDenominator": 1,
        "zeroDenominatorBehavior": "unknown",
    }
    with pytest.raises(ContractViolation, match="RATIO_EVENT_COLLISION"):
        validate_closure_semantics(metric)


def test_release_evidence_expiry_fails_closed() -> None:
    release = _base("ReleaseEvidence") | {
        "gateId": "GATE-001",
        "gateRegistryVersion": "1.0.0",
        "gateRegistryDigest": "sha256:" + "b" * 64,
        "applicability": "operational_thread",
        "scope": "ingress",
        "releaseDigest": "sha256:" + "c" * 64,
        "testVersion": "suite/1.0.0",
        "outcome": "pass",
        "evidenceRefs": ["run-1"],
        "ownerId": "owner-1",
        "expiresAt": "2030-01-01T00:00:00Z",
    }
    with pytest.raises(ContractViolation, match="RELEASE_EVIDENCE_EXPIRED"):
        validate_closure_semantics(release, now=datetime(2030, 1, 2, tzinfo=UTC))


def test_accessibility_waiver_requires_legal_basis_for_protected_obligation() -> None:
    waiver = _base("AccessibilityEvidence") | {
        "standard": "WCAG 2.2 AA",
        "suiteVersion": "suite/1",
        "surface": "web",
        "buildDigest": "sha256:" + "a" * 64,
        "releaseDigest": "sha256:" + "b" * 64,
        "assistiveTechnologies": ["keyboard"],
        "knownExceptions": ["focus exception"],
        "outcome": "waived",
        "ownerId": "owner-1",
        "expiresAt": "2030-02-01T00:00:00Z",
        "waiverScope": ["checkout-dialog"],
        "affectedObligations": ["focus_visibility"],
        "waiverReason": "temporary vendor defect",
        "compensatingControl": "operator-assisted flow",
        "waiverApproverAuthorityClass": "accessibility_legal",
        "waiverApproverId": "legal-1",
    }
    assert not list(Draft202012Validator(_schema()).iter_errors(waiver))
    with pytest.raises(ContractViolation, match="ACCESSIBILITY_WAIVER_LEGAL_BASIS"):
        validate_closure_semantics(waiver, now=datetime(2030, 1, 2, tzinfo=UTC))
    waiver["legalBasis"] = "documented-equivalent-access-determination"
    validate_closure_semantics(waiver, now=datetime(2030, 1, 2, tzinfo=UTC))


def test_output_route_stale_label_is_conditionally_admitted() -> None:
    mapping = _base("OutputClassMapping") | {
        "expiresAt": "2030-02-01T00:00:00Z",
        "actionClass": "lead_qualification",
        "outputClass": "qualification_advice",
        "policyVersion": "route-policy/1",
        "allowedArtifactTypes": ["qualification_summary"],
        "requiredAuthorityClasses": ["read_buyer_context"],
        "allowedEpistemicTypes": ["verified_fact"],
        "staleEvidencePolicy": "allow_labeled",
        "groundingMode": "all_claims_grounded",
        "effectEligibility": "none",
    }
    errors = list(Draft202012Validator(_schema()).iter_errors(mapping))
    assert errors
    mapping["staleLabel"] = "stale_source"
    assert not list(Draft202012Validator(_schema()).iter_errors(mapping))


def test_generated_closure_fixtures_cover_every_admitted_record_type() -> None:
    valid = json.loads(Path("tests/fixtures/generated/closure_1_1_valid.json").read_text())
    invalid = json.loads(Path("tests/fixtures/generated/closure_1_1_invalid.json").read_text())
    expected = {
        "ExternalMessageIdentity",
        "CapabilityInventory",
        "EffectDraftPreview",
        "ContextSourceFreshness",
        "OutputClassMapping",
        "MetricDefinition",
        "MetricObservation",
        "ReleaseEvidence",
        "AccessibilityEvidence",
    }
    assert set(valid) == expected
    assert set(invalid) == expected
    validator = Draft202012Validator(_schema())
    for name in sorted(expected):
        assert not list(validator.iter_errors(valid[name])), name
        assert list(validator.iter_errors(invalid[name]["record"])), name
