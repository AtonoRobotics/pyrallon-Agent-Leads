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
        "schemaVersion": "open-019-024/1.0.0",
        "tenantId": "tenant-1",
        "recordId": record_type.lower(),
        "observedAt": "2030-01-01T00:00:00Z",
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
        "applicability": "operational_thread",
        "scope": "ingress",
        "testVersion": "suite/1.0.0",
        "outcome": "pass",
        "evidenceRefs": ["run-1"],
        "ownerId": "owner-1",
        "expiresAt": "2030-01-01T00:00:00Z",
    }
    with pytest.raises(ContractViolation, match="RELEASE_EVIDENCE_EXPIRED"):
        validate_closure_semantics(release, now=datetime(2030, 1, 2, tzinfo=UTC))

