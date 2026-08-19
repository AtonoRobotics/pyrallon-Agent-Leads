from __future__ import annotations

import copy

import pytest

from buyer_ops_contracts.errors import ContractViolation
from buyer_ops_contracts.telemetry import RatioEvent, RatioTelemetryRecorder


def _definition() -> dict:
    return {
        "schemaVersion": "open-019-024/1.1.0",
        "tenantId": "tenant-1",
        "recordId": "metric-definition-1",
        "recordVersion": 1,
        "observedAt": "2030-01-01T00:00:00Z",
        "effectiveFrom": "2030-01-01T00:00:00Z",
        "status": "current",
        "evidenceRefs": ["metric-catalog-1"],
        "recordType": "MetricDefinition",
        "metricId": "qualification_completion_ratio",
        "unit": "ratio",
        "numeratorEvent": "qualification_completed",
        "denominatorEvent": "qualification_started",
        "correlationKey": "journey_id",
        "dimensions": ["channel"],
        "window": "rolling_24h",
        "minimumDenominator": 2,
        "zeroDenominatorBehavior": "unknown",
    }


class _Cursor:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        self.statements.append((statement, parameters))


class _Connection:
    def __init__(self) -> None:
        self.cursor_instance = _Cursor()
        self.commits = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        raise AssertionError("rollback was not expected")


def _event(event_id: str, event_type: str, journey: str) -> RatioEvent:
    return RatioEvent(event_id, event_type, journey, {"channel": "web"})


def test_ratio_observation_is_derived_from_bound_event_sets() -> None:
    connection = _Connection()
    observation = RatioTelemetryRecorder(connection, tenant_id="tenant-1").record(
        _definition(),
        observation_id="observation-1",
        observed_at="2030-01-01T01:00:00Z",
        window_start="2030-01-01T00:00:00Z",
        window_end="2030-01-01T01:00:00Z",
        numerator_events=[_event("complete-1", "qualification_completed", "journey-1")],
        denominator_events=[
            _event("start-1", "qualification_started", "journey-1"),
            _event("start-2", "qualification_started", "journey-2"),
        ],
        dimension_values={"channel": "web"},
        evidence_refs=["event-export-1"],
    )
    assert observation["numerator"] == 1
    assert observation["denominator"] == 2
    assert observation["value"] == 0.5
    assert observation["calculationState"] == "value"
    assert observation["numeratorEventDigest"].startswith("sha256:")
    assert connection.commits == 1


def test_ratio_observation_fails_on_definition_or_correlation_drift() -> None:
    connection = _Connection()
    recorder = RatioTelemetryRecorder(connection, tenant_id="tenant-1")
    with pytest.raises(ContractViolation, match="RATIO_CORRELATION_MISMATCH"):
        recorder.record(
            _definition(),
            observation_id="observation-1",
            observed_at="2030-01-01T01:00:00Z",
            window_start="2030-01-01T00:00:00Z",
            window_end="2030-01-01T01:00:00Z",
            numerator_events=[_event("complete-1", "qualification_completed", "journey-3")],
            denominator_events=[_event("start-1", "qualification_started", "journey-1")],
            dimension_values={"channel": "web"},
            evidence_refs=["event-export-1"],
        )
    changed = copy.deepcopy(_definition())
    changed["dimensions"] = ["provider"]
    with pytest.raises(ContractViolation, match="METRIC_DIMENSIONS_MISMATCH"):
        recorder.record(
            changed,
            observation_id="observation-2",
            observed_at="2030-01-01T01:00:00Z",
            window_start="2030-01-01T00:00:00Z",
            window_end="2030-01-01T01:00:00Z",
            numerator_events=[],
            denominator_events=[],
            dimension_values={"channel": "web"},
            evidence_refs=["event-export-1"],
        )


@pytest.mark.parametrize(
    ("behavior", "expected_state", "expected_value"),
    [
        ("unknown", "unknown", None),
        ("not_applicable", "not_applicable", None),
        ("zero", "value", 0.0),
    ],
)
def test_zero_denominator_behavior_is_definition_driven(
    behavior: str, expected_state: str, expected_value: float | None
) -> None:
    definition = _definition()
    definition["zeroDenominatorBehavior"] = behavior
    observation = RatioTelemetryRecorder(_Connection(), tenant_id="tenant-1").record(
        definition,
        observation_id=f"observation-{behavior}",
        observed_at="2030-01-01T01:00:00Z",
        window_start="2030-01-01T00:00:00Z",
        window_end="2030-01-01T01:00:00Z",
        numerator_events=[],
        denominator_events=[],
        dimension_values={"channel": "web"},
        evidence_refs=["event-export-1"],
    )
    assert observation["calculationState"] == expected_state
    assert observation.get("value") == expected_value
