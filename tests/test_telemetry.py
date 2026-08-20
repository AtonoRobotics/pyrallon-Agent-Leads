from __future__ import annotations

import copy
from typing import Any

import pytest

from buyer_ops_contracts.errors import ContractViolation
from buyer_ops_contracts.telemetry import (
    LatencyAlertEvaluator,
    LatencySloEvaluator,
    TelemetryRecorder,
    load_metric_catalog,
    validate_dashboard_definition,
)


class _Cursor:
    def __init__(self, series_row: tuple[bool, int] = (False, 0)) -> None:
        self.series_row = series_row
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        self.statements.append((statement, parameters))

    def fetchone(self) -> tuple[bool, int]:
        return self.series_row


class _Connection:
    def __init__(self, series_row: tuple[bool, int] = (False, 0)) -> None:
        self.cursor_instance = _Cursor(series_row)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _Cursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _duration_observation() -> dict[str, Any]:
    return {
        "messageType": "metric_observation",
        "schemaVersion": "telemetry-slo/1.0.0",
        "observationId": "capture-latency-1",
        "metricId": "capture_latency_seconds",
        "value": 12,
        "unit": "seconds",
        "eventStartedAt": "2030-01-01T00:00:00Z",
        "eventEndedAt": "2030-01-01T00:00:12Z",
        "observedAt": "2030-01-01T00:00:13Z",
        "dimensions": {"environment": "production", "channel": "web"},
        "sourceEventIds": ["ingress-1", "capture-1"],
        "producerId": "ingress-service",
        "retentionClass": "operational_90d",
    }


def test_metric_observation_binds_catalog_unit_retention_and_elapsed_time() -> None:
    connection = _Connection()
    result = TelemetryRecorder(connection, tenant_id="tenant-1").record_observation(
        _duration_observation()
    )
    assert result["value"] == 12
    assert connection.commits == 1
    assert any(
        "pg_advisory_xact_lock" in statement
        for statement, _ in connection.cursor_instance.statements
    )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("unit", "count", "METRIC_UNIT_MISMATCH"),
        ("retentionClass", "audit_7y", "METRIC_RETENTION_MISMATCH"),
        ("value", 11, "METRIC_DURATION_MISMATCH"),
        ("eventEndedAt", "2029-12-31T23:59:59Z", "METRIC_EVENT_ORDER"),
    ],
)
def test_metric_observation_rejects_catalog_drift(field: str, value: object, code: str) -> None:
    observation = _duration_observation()
    observation[field] = value
    with pytest.raises(ContractViolation, match=code):
        TelemetryRecorder(_Connection(), tenant_id="tenant-1").record_observation(observation)


def test_count_metric_requires_a_non_negative_integer() -> None:
    observation = _duration_observation()
    observation.update(
        {
            "metricId": "duplicate_suppression_total",
            "unit": "count",
            "value": 1.5,
            "eventEndedAt": observation["eventStartedAt"],
        }
    )
    with pytest.raises(ContractViolation, match="METRIC_COUNT_INVALID"):
        TelemetryRecorder(_Connection(), tenant_id="tenant-1").record_observation(observation)


def test_new_series_is_rejected_at_catalog_limit() -> None:
    catalog = copy.deepcopy(load_metric_catalog())
    catalog["dimensionPolicy"]["maximumSeriesPerMetric"] = 1
    connection = _Connection(series_row=(False, 1))
    with pytest.raises(ContractViolation, match="SERIES_LIMIT"):
        TelemetryRecorder(connection, tenant_id="tenant-1", catalog=catalog).record_observation(
            _duration_observation()
        )
    assert connection.rollbacks == 1


def test_existing_series_remains_admissible_at_catalog_limit() -> None:
    catalog = copy.deepcopy(load_metric_catalog())
    catalog["dimensionPolicy"]["maximumSeriesPerMetric"] = 1
    connection = _Connection(series_row=(True, 1))
    TelemetryRecorder(connection, tenant_id="tenant-1", catalog=catalog).record_observation(
        _duration_observation()
    )
    assert connection.commits == 1


def _latency_observation(observation_id: str, seconds: int) -> dict[str, Any]:
    observation = _duration_observation()
    observation["observationId"] = observation_id
    observation["value"] = seconds
    observation["eventEndedAt"] = f"2030-01-01T00:00:{seconds:02d}Z"
    return observation


def _evaluate(observations: list[dict[str, Any]], evaluation_id: str = "evaluation-1") -> dict:
    return LatencySloEvaluator().evaluate(
        "capture_p95",
        observations,
        evaluation_id=evaluation_id,
        window_started_at="2030-01-01T00:00:00Z",
        window_ended_at="2030-01-01T00:30:00Z",
        evaluated_at="2030-01-01T00:30:01Z",
    )


def test_latency_slo_uses_nearest_rank_and_threshold_exceedance_fraction() -> None:
    observations = [
        _latency_observation(f"latency-{index}", 10 if index < 18 else 40) for index in range(20)
    ]
    evaluation = _evaluate(observations)
    assert evaluation["sampleCount"] == 20
    assert evaluation["actual"] == 40
    assert evaluation["status"] == "fail"
    assert evaluation["errorBudgetConsumed"] == 0.1


def test_latency_slo_is_insufficient_below_catalog_minimum() -> None:
    evaluation = _evaluate([_latency_observation(f"latency-{index}", 10) for index in range(19)])
    assert evaluation["sampleCount"] == 19
    assert evaluation["actual"] is None
    assert evaluation["status"] == "insufficient_data"
    assert evaluation["errorBudgetConsumed"] is None


def test_latency_slo_window_is_half_open_and_late_recomputation_is_digest_bound() -> None:
    initial = [_latency_observation(f"latency-{index}", 10) for index in range(20)]
    boundary = _latency_observation("latency-at-window-end", 10)
    boundary["eventStartedAt"] = "2030-01-01T00:29:50Z"
    boundary["eventEndedAt"] = "2030-01-01T00:30:00Z"
    first = _evaluate([*initial, boundary], "evaluation-before-late-event")
    assert first["sampleCount"] == 20

    late = _latency_observation("latency-late-arrival", 20)
    late["observedAt"] = "2030-01-01T00:40:00Z"
    recomputed = _evaluate([*initial, late], "evaluation-after-late-event")
    assert recomputed["sampleCount"] == 21
    assert recomputed["sourceDigest"] != first["sourceDigest"]


def test_latency_slo_rejects_window_and_source_drift() -> None:
    with pytest.raises(ContractViolation, match="SLO_WINDOW_MISMATCH"):
        LatencySloEvaluator().evaluate(
            "capture_p95",
            [],
            evaluation_id="evaluation-bad-window",
            window_started_at="2030-01-01T00:00:00Z",
            window_ended_at="2030-01-01T00:29:59Z",
            evaluated_at="2030-01-01T00:30:01Z",
        )

    wrong_metric = _latency_observation("wrong-metric", 10)
    wrong_metric["metricId"] = "acknowledgment_latency_seconds"
    with pytest.raises(ContractViolation, match="SLO_SOURCE_METRIC_MISMATCH"):
        _evaluate([wrong_metric])


def test_ratio_slo_remains_fail_closed_without_published_observation_binding() -> None:
    with pytest.raises(ValueError, match="do not bind this SLO input shape"):
        LatencySloEvaluator().evaluate(
            "provider_unknown_ratio",
            [],
            evaluation_id="ratio-evaluation",
            window_started_at="2030-01-01T00:00:00Z",
            window_ended_at="2030-01-02T00:00:00Z",
            evaluated_at="2030-01-02T00:00:01Z",
        )


def _dashboard() -> dict[str, Any]:
    return {
        "messageType": "dashboard_definition",
        "schemaVersion": "telemetry-slo/1.0.0",
        "dashboardId": "ot01_operations",
        "version": 1,
        "owner": "product_operations",
        "metricIds": [
            "capture_latency_seconds",
            "acknowledgment_latency_seconds",
            "identity_ambiguity_ratio",
            "qualification_completion_ratio",
            "consult_readiness_ratio",
            "slot_conversion_ratio",
            "agent_exception_load",
        ],
        "sloIds": ["capture_p95", "acknowledgment_p95"],
        "refreshSeconds": 30,
        "retentionClass": "audit_7y",
    }


def test_dashboard_definition_binds_exact_catalog_owner_metrics_and_slos() -> None:
    validate_dashboard_definition(_dashboard())

    wrong_owner = {**_dashboard(), "owner": "platform_operations"}
    with pytest.raises(ContractViolation, match="DASHBOARD_OWNER_MISMATCH"):
        validate_dashboard_definition(wrong_owner)

    missing_metric = _dashboard()
    missing_metric["metricIds"] = missing_metric["metricIds"][:-1]
    with pytest.raises(ContractViolation, match="DASHBOARD_METRIC_MISMATCH"):
        validate_dashboard_definition(missing_metric)

    unrelated_slo = {**_dashboard(), "sloIds": ["provider_unknown_ratio"]}
    with pytest.raises(ContractViolation, match="DASHBOARD_SLO_METRIC_MISMATCH"):
        validate_dashboard_definition(unrelated_slo)


def test_latency_alerts_bind_catalog_threshold_owner_route_and_retention() -> None:
    warning_evaluation = _evaluate(
        [_latency_observation(f"warning-{index}", 10 if index < 10 else 40) for index in range(20)],
        "evaluation-warning",
    )
    alerts = LatencyAlertEvaluator().evaluate(
        warning_evaluation,
        alert_ids={"slo_warning": "alert-warning"},
        opened_at="2030-01-01T00:30:02Z",
    )
    assert alerts == [
        {
            "messageType": "alert_event",
            "schemaVersion": "telemetry-slo/1.0.0",
            "alertId": "alert-warning",
            "policyId": "slo_warning",
            "sloEvaluationId": "evaluation-warning",
            "severity": "warning",
            "owner": "platform_operations",
            "route": "ticket",
            "openedAt": "2030-01-01T00:30:02Z",
            "state": "open",
            "retentionClass": "audit_7y",
        }
    ]

    exhausted_evaluation = _evaluate(
        [_latency_observation(f"exhausted-{index}", 40) for index in range(20)],
        "evaluation-exhausted",
    )
    exhausted = LatencyAlertEvaluator().evaluate(
        exhausted_evaluation,
        alert_ids={
            "slo_warning": "alert-warning-2",
            "slo_exhausted": "alert-critical",
        },
        opened_at="2030-01-01T00:30:02Z",
    )
    assert [alert["policyId"] for alert in exhausted] == ["slo_warning", "slo_exhausted"]
    assert exhausted[1]["severity"] == "critical"
    assert exhausted[1]["route"] == "pager"


def test_insufficient_latency_slo_does_not_open_alert() -> None:
    insufficient = _evaluate(
        [_latency_observation(f"insufficient-{index}", 10) for index in range(19)]
    )
    assert (
        LatencyAlertEvaluator().evaluate(
            insufficient,
            alert_ids={},
            opened_at="2030-01-01T00:30:02Z",
        )
        == []
    )


def test_alert_evaluator_rejects_unpublished_condition_language() -> None:
    catalog = copy.deepcopy(load_metric_catalog())
    catalog["alerts"][0]["when"] = "errorBudgetConsumed>0.5"
    evaluation = _evaluate([_latency_observation(f"condition-{index}", 40) for index in range(20)])
    with pytest.raises(ContractViolation, match="ALERT_POLICY_UNSUPPORTED"):
        LatencyAlertEvaluator(catalog).evaluate(
            evaluation,
            alert_ids={"slo_warning": "alert-warning"},
            opened_at="2030-01-01T00:30:02Z",
        )
