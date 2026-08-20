"""Catalog-only telemetry observations. Prohibited dimensions fail closed."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any, cast

import rfc8785
from psycopg.types.json import Jsonb

from .canonical_repository import Connection
from .closure import validate_closure_semantics
from .digest import sha256_digest
from .errors import ContractViolation, Violation
from .structural import validate_record

_CATALOG = json.loads(
    files("buyer_ops_contracts")
    .joinpath("schemas")
    .joinpath("telemetry_slo.schema.json")
    .read_text()
)
# Catalog of metric identities lives next to the schema in the spec root; the
# packaged copy is loaded from the repo when running in-tree.
ALLOWED_DIMENSIONS = frozenset({"environment", "region", "channel", "provider", "result"})
PROHIBITED_DIMENSIONS = frozenset(
    {"tenant_id", "person_id", "journey_id", "message_id", "free_text"}
)


@dataclass(frozen=True, slots=True)
class RatioEvent:
    event_id: str
    event_type: str
    correlation_value: str
    dimensions: dict[str, str]


def _event_set_digest(events: list[RatioEvent]) -> str:
    identities = sorted(event.event_id for event in events)
    return f"sha256:{hashlib.sha256(rfc8785.dumps(identities)).hexdigest()}"


def _correlation_digest(events: list[RatioEvent]) -> str:
    identities = sorted({event.correlation_value for event in events})
    return f"sha256:{hashlib.sha256(rfc8785.dumps(identities)).hexdigest()}"


class RatioTelemetryRecorder:
    """Construct and persist evidence-bearing ratio observations from declared event sets."""

    def __init__(self, connection: Connection, *, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._connection = connection
        self._tenant_id = tenant_id

    def record(
        self,
        definition: dict[str, Any],
        *,
        observation_id: str,
        observed_at: str,
        window_start: str,
        window_end: str,
        numerator_events: list[RatioEvent],
        denominator_events: list[RatioEvent],
        dimension_values: dict[str, str],
        evidence_refs: list[str],
    ) -> dict[str, Any]:
        validate_record(definition, "closure")
        evaluated_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).astimezone(UTC)
        validate_closure_semantics(definition, now=evaluated_at)
        if (
            definition.get("recordType") != "MetricDefinition"
            or definition.get("tenantId") != self._tenant_id
            or definition.get("status") != "current"
            or definition.get("unit") not in {"ratio", "percentage"}
        ):
            raise ContractViolation(
                [
                    Violation(
                        "METRIC_DEFINITION_INVALID",
                        "$.recordType",
                        "current ratio definition required",
                    )
                ]
            )
        if set(dimension_values) != set(definition["dimensions"]):
            raise ContractViolation(
                [
                    Violation(
                        "METRIC_DIMENSIONS_MISMATCH",
                        "$.dimensionValues",
                        "must exactly match definition dimensions",
                    )
                ]
            )
        self._validate_events(definition, numerator_events, denominator_events, dimension_values)
        numerator = len(numerator_events)
        denominator = len(denominator_events)
        state, value = self._calculate(definition, numerator, denominator)
        observation: dict[str, Any] = {
            "schemaVersion": "open-019-024/1.1.0",
            "tenantId": self._tenant_id,
            "recordId": observation_id,
            "recordVersion": 1,
            "observedAt": observed_at,
            "effectiveFrom": observed_at,
            "status": "current",
            "evidenceRefs": evidence_refs,
            "recordType": "MetricObservation",
            "metricId": definition["metricId"],
            "metricDefinitionRecordId": definition["recordId"],
            "metricDefinitionRecordVersion": definition["recordVersion"],
            "window": definition["window"],
            "windowStart": window_start,
            "windowEnd": window_end,
            "numerator": numerator,
            "denominator": denominator,
            "numeratorEvent": definition["numeratorEvent"],
            "denominatorEvent": definition["denominatorEvent"],
            "correlationKey": definition["correlationKey"],
            "correlationDigest": _correlation_digest(denominator_events),
            "numeratorEventDigest": _event_set_digest(numerator_events),
            "denominatorEventDigest": _event_set_digest(denominator_events),
            "dimensionValues": dimension_values,
            "calculationState": state,
        }
        if value is not None:
            observation["value"] = value
        validate_record(observation, "closure")
        validate_closure_semantics(observation, now=evaluated_at)
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
                cursor.execute(
                    """
                    INSERT INTO telemetry_observations (
                        tenant_id, observation_id, metric_id, payload, observed_at, source_digest
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """.strip(),
                    (
                        self._tenant_id,
                        observation_id,
                        definition["metricId"],
                        Jsonb(observation),
                        observed_at,
                        sha256_digest(observation),
                    ),
                )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return observation

    @staticmethod
    def _validate_events(
        definition: dict[str, Any],
        numerator_events: list[RatioEvent],
        denominator_events: list[RatioEvent],
        dimensions: dict[str, str],
    ) -> None:
        if any(event.event_type != definition["numeratorEvent"] for event in numerator_events):
            raise ContractViolation(
                [
                    Violation(
                        "NUMERATOR_EVENT_MISMATCH",
                        "$.numeratorEvent",
                        "event type differs from definition",
                    )
                ]
            )
        if any(event.event_type != definition["denominatorEvent"] for event in denominator_events):
            raise ContractViolation(
                [
                    Violation(
                        "DENOMINATOR_EVENT_MISMATCH",
                        "$.denominatorEvent",
                        "event type differs from definition",
                    )
                ]
            )
        if any(
            event.dimensions != dimensions for event in [*numerator_events, *denominator_events]
        ):
            raise ContractViolation(
                [
                    Violation(
                        "EVENT_DIMENSIONS_MISMATCH", "$.dimensionValues", "event dimensions differ"
                    )
                ]
            )
        numerator_correlations = {event.correlation_value for event in numerator_events}
        denominator_correlations = {event.correlation_value for event in denominator_events}
        if not numerator_correlations <= denominator_correlations:
            raise ContractViolation(
                [
                    Violation(
                        "RATIO_CORRELATION_MISMATCH",
                        "$.correlationKey",
                        "numerator correlations must be a denominator subset",
                    )
                ]
            )

    @staticmethod
    def _calculate(
        definition: dict[str, Any], numerator: int, denominator: int
    ) -> tuple[str, float | None]:
        if denominator == 0:
            behavior = definition["zeroDenominatorBehavior"]
            if behavior == "zero":
                return "value", 0.0
            return behavior, None
        if denominator < definition["minimumDenominator"]:
            return "unknown", None
        value = numerator / denominator
        if definition["unit"] == "percentage":
            value *= 100
        return "value", value


def load_metric_catalog(path: Any | None = None) -> dict[str, Any]:
    if path is not None:
        return cast(dict[str, Any], json.loads(path.read_text()))
    return cast(
        dict[str, Any],
        json.loads(files("buyer_ops_contracts").joinpath("telemetry_catalog.json").read_text()),
    )


class TelemetryRecorder:
    def __init__(
        self,
        connection: Connection,
        *,
        tenant_id: str,
        catalog: dict[str, Any] | None = None,
    ) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._connection = connection
        self._tenant_id = tenant_id
        self._catalog = catalog or load_metric_catalog()
        self._metrics = {metric["id"]: metric for metric in self._catalog["metrics"]}

    def record_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        validate_record(observation, "telemetry_slo")
        if observation.get("messageType") != "metric_observation":
            raise ContractViolation(
                [
                    Violation(
                        "TELEMETRY_TYPE",
                        "$.messageType",
                        "only metric_observation is admitted here",
                    )
                ]
            )
        metric_id = observation["metricId"]
        metric = self._metrics.get(metric_id)
        if metric is None:
            raise ContractViolation(
                [Violation("UNKNOWN_METRIC", "$.metricId", f"{metric_id} is not in the catalog")]
            )
        if observation["unit"] != metric["unit"]:
            raise ContractViolation(
                [
                    Violation(
                        "METRIC_UNIT_MISMATCH",
                        "$.unit",
                        f"must equal catalog unit {metric['unit']}",
                    )
                ]
            )
        if observation["retentionClass"] != metric["retention"]:
            raise ContractViolation(
                [
                    Violation(
                        "METRIC_RETENTION_MISMATCH",
                        "$.retentionClass",
                        f"must equal catalog retention {metric['retention']}",
                    )
                ]
            )
        if metric["unit"] == "ratio":
            raise ContractViolation(
                [
                    Violation(
                        "RATIO_EVENT_SETS_REQUIRED",
                        "$.unit",
                        "ratio observations must be constructed by RatioTelemetryRecorder",
                    )
                ]
            )
        started = _timestamp(observation["eventStartedAt"])
        ended = _timestamp(observation["eventEndedAt"])
        if ended < started:
            raise ContractViolation(
                [Violation("METRIC_EVENT_ORDER", "$.eventEndedAt", "must not precede start")]
            )
        if metric["unit"] == "seconds":
            elapsed = (ended - started).total_seconds()
            if observation["value"] != elapsed:
                raise ContractViolation(
                    [
                        Violation(
                            "METRIC_DURATION_MISMATCH",
                            "$.value",
                            "must equal non-negative elapsed event seconds",
                        )
                    ]
                )
        elif metric["unit"] == "count" and (
            observation["value"] < 0 or not float(observation["value"]).is_integer()
        ):
            raise ContractViolation(
                [Violation("METRIC_COUNT_INVALID", "$.value", "must be a non-negative integer")]
            )
        dimensions = observation.get("dimensions", {})
        illegal = set(dimensions) & PROHIBITED_DIMENSIONS
        if illegal:
            raise ContractViolation(
                [
                    Violation(
                        "PROHIBITED_DIMENSION",
                        "$.dimensions",
                        f"dimensions {sorted(illegal)} are prohibited",
                    )
                ]
            )
        extra = set(dimensions) - ALLOWED_DIMENSIONS
        if extra:
            raise ContractViolation(
                [
                    Violation(
                        "UNKNOWN_DIMENSION",
                        "$.dimensions",
                        f"dimensions {sorted(extra)} are not cataloged",
                    )
                ]
            )
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"telemetry-series:{self._tenant_id}:{metric_id}",),
                )
                cursor.execute(
                    """
                    SELECT
                        EXISTS (
                            SELECT 1 FROM telemetry_observations
                            WHERE tenant_id = %s AND metric_id = %s
                              AND payload->'dimensions' = %s
                        ),
                        COUNT(DISTINCT payload->'dimensions')
                    FROM telemetry_observations
                    WHERE tenant_id = %s AND metric_id = %s
                    """.strip(),
                    (
                        self._tenant_id,
                        metric_id,
                        Jsonb(dimensions),
                        self._tenant_id,
                        metric_id,
                    ),
                )
                series_row = cursor.fetchone()
                if series_row is None:
                    raise RuntimeError("telemetry series count unavailable")
                exists, series_count = bool(series_row[0]), cast(int, series_row[1])
                maximum = int(self._catalog["dimensionPolicy"]["maximumSeriesPerMetric"])
                if not exists and series_count >= maximum:
                    raise ContractViolation(
                        [Violation("SERIES_LIMIT", "$.dimensions", "series cardinality exceeded")]
                    )
                cursor.execute(
                    """
                    INSERT INTO telemetry_observations (
                        tenant_id, observation_id, metric_id, payload, observed_at, source_digest
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """.strip(),
                    (
                        self._tenant_id,
                        observation["observationId"],
                        metric_id,
                        Jsonb(observation),
                        observation["eventEndedAt"],
                        sha256_digest(observation),
                    ),
                )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return observation


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("telemetry timestamp must include an offset")
    return parsed.astimezone(UTC)


class LatencySloEvaluator:
    """Evaluate only cataloged latency SLOs whose inputs are executable in telemetry-slo/1.0.0."""

    def __init__(self, catalog: dict[str, Any] | None = None) -> None:
        self._catalog = catalog or load_metric_catalog()
        self._metrics = {metric["id"]: metric for metric in self._catalog["metrics"]}
        self._slos = {slo["id"]: slo for slo in self._catalog["slos"]}

    def evaluate(
        self,
        slo_id: str,
        observations: list[dict[str, Any]],
        *,
        evaluation_id: str,
        window_started_at: str,
        window_ended_at: str,
        evaluated_at: str,
    ) -> dict[str, Any]:
        slo = self._slos.get(slo_id)
        if slo is None:
            raise ContractViolation(
                [Violation("UNKNOWN_SLO", "$.sloId", f"{slo_id} is not in the catalog")]
            )
        metric = self._metrics[slo["metricId"]]
        if metric["unit"] != "seconds" or slo["statistic"] not in {"p50", "p95", "p99"}:
            raise ValueError("the published schemas do not bind this SLO input shape")
        window_start = _timestamp(window_started_at)
        window_end = _timestamp(window_ended_at)
        if window_end <= window_start:
            raise ContractViolation(
                [Violation("SLO_WINDOW_ORDER", "$.windowEndedAt", "must follow window start")]
            )
        if slo["window"] == "rolling_30m" and (window_end - window_start).total_seconds() != 1800:
            raise ContractViolation(
                [
                    Violation(
                        "SLO_WINDOW_MISMATCH",
                        "$.windowEndedAt",
                        "rolling_30m requires an exact 30-minute half-open window",
                    )
                ]
            )
        if _timestamp(evaluated_at) < window_end:
            raise ContractViolation(
                [
                    Violation(
                        "SLO_EVALUATED_BEFORE_WINDOW_END",
                        "$.evaluatedAt",
                        "evaluation cannot precede window end",
                    )
                ]
            )

        admitted: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for observation in observations:
            validate_record(observation, "telemetry_slo")
            if observation.get("messageType") != "metric_observation":
                raise ContractViolation(
                    [Violation("SLO_SOURCE_TYPE", "$", "source must be a metric observation")]
                )
            if observation["metricId"] != metric["id"] or observation["unit"] != "seconds":
                raise ContractViolation(
                    [
                        Violation(
                            "SLO_SOURCE_METRIC_MISMATCH",
                            "$.metricId",
                            "source metric and unit must match the cataloged SLO metric",
                        )
                    ]
                )
            if observation["retentionClass"] != metric["retention"]:
                raise ContractViolation(
                    [
                        Violation(
                            "METRIC_RETENTION_MISMATCH",
                            "$.retentionClass",
                            "source retention must match the metric catalog",
                        )
                    ]
                )
            source_started = _timestamp(observation["eventStartedAt"])
            source_ended = _timestamp(observation["eventEndedAt"])
            elapsed = (source_ended - source_started).total_seconds()
            if elapsed < 0 or observation["value"] != elapsed:
                raise ContractViolation(
                    [
                        Violation(
                            "METRIC_DURATION_MISMATCH",
                            "$.value",
                            "source must equal non-negative elapsed event seconds",
                        )
                    ]
                )
            observation_id = str(observation["observationId"])
            if observation_id in seen_ids:
                raise ContractViolation(
                    [
                        Violation(
                            "DUPLICATE_SLO_SOURCE",
                            "$.observationId",
                            "an observation may contribute at most once",
                        )
                    ]
                )
            seen_ids.add(observation_id)
            if window_start <= source_ended < window_end:
                admitted.append(observation)

        values = sorted(float(observation["value"]) for observation in admitted)
        sample_count = len(values)
        minimum = int(slo["minimumSamples"])
        actual: float | None = None
        error_budget: float | None = None
        status = str(slo["noData"])
        if sample_count >= minimum:
            percentile = int(str(slo["statistic"])[1:]) / 100
            rank = max(1, math.ceil(percentile * sample_count))
            actual = values[rank - 1]
            objective = float(slo["objective"])
            comparator = str(slo["comparator"])
            passed = (
                (comparator == "lte" and actual <= objective)
                or (comparator == "gte" and actual >= objective)
                or (comparator == "eq" and actual == objective)
            )
            status = "pass" if passed else "fail"
            error_budget = sum(value > objective for value in values) / sample_count

        sources = [
            {
                "observationId": observation["observationId"],
                "digest": sha256_digest(observation),
            }
            for observation in sorted(admitted, key=lambda item: str(item["observationId"]))
        ]
        evaluation = {
            "messageType": "slo_evaluation",
            "schemaVersion": "telemetry-slo/1.0.0",
            "evaluationId": evaluation_id,
            "sloId": slo_id,
            "catalogVersion": self._catalog["catalogVersion"],
            "windowStartedAt": window_started_at,
            "windowEndedAt": window_ended_at,
            "sampleCount": sample_count,
            "objective": slo["objective"],
            "actual": actual,
            "comparator": slo["comparator"],
            "status": status,
            "errorBudgetConsumed": error_budget,
            "sourceDigest": sha256_digest(sources),
            "evaluatedAt": evaluated_at,
        }
        validate_record(evaluation, "telemetry_slo")
        return evaluation


def validate_dashboard_definition(
    dashboard: dict[str, Any], catalog: dict[str, Any] | None = None
) -> None:
    """Require a dashboard to use the exact catalog identity, owner, and metric inventory."""
    active = catalog or load_metric_catalog()
    validate_record(dashboard, "telemetry_slo")
    if dashboard.get("messageType") != "dashboard_definition":
        raise ContractViolation(
            [Violation("TELEMETRY_TYPE", "$.messageType", "dashboard definition required")]
        )
    definitions = {item["id"]: item for item in active["dashboards"]}
    definition = definitions.get(dashboard["dashboardId"])
    if definition is None:
        raise ContractViolation(
            [
                Violation(
                    "UNKNOWN_DASHBOARD",
                    "$.dashboardId",
                    "dashboard identity is not in the telemetry catalog",
                )
            ]
        )
    if dashboard["owner"] != definition["owner"]:
        raise ContractViolation(
            [
                Violation(
                    "DASHBOARD_OWNER_MISMATCH",
                    "$.owner",
                    "owner must equal the cataloged dashboard owner",
                )
            ]
        )
    if set(dashboard["metricIds"]) != set(definition["metrics"]):
        raise ContractViolation(
            [
                Violation(
                    "DASHBOARD_METRIC_MISMATCH",
                    "$.metricIds",
                    "metric inventory must exactly equal the cataloged dashboard inventory",
                )
            ]
        )
    slos = {item["id"]: item for item in active["slos"]}
    unknown = sorted(set(dashboard["sloIds"]) - set(slos))
    if unknown:
        raise ContractViolation(
            [Violation("UNKNOWN_SLO", "$.sloIds", f"unknown SLO identities: {unknown}")]
        )
    unrelated = sorted(
        slo_id
        for slo_id in dashboard["sloIds"]
        if slos[slo_id]["metricId"] not in set(dashboard["metricIds"])
    )
    if unrelated:
        raise ContractViolation(
            [
                Violation(
                    "DASHBOARD_SLO_METRIC_MISMATCH",
                    "$.sloIds",
                    f"SLO metrics are absent from the dashboard: {unrelated}",
                )
            ]
        )


class LatencyAlertEvaluator:
    """Materialize cataloged warning and exhausted-budget alerts for latency SLOs."""

    def __init__(self, catalog: dict[str, Any] | None = None) -> None:
        self._catalog = catalog or load_metric_catalog()
        self._slos = {item["id"]: item for item in self._catalog["slos"]}
        self._policies = {item["id"]: item for item in self._catalog["alerts"]}

    def evaluate(
        self,
        evaluation: dict[str, Any],
        *,
        alert_ids: dict[str, str],
        opened_at: str,
    ) -> list[dict[str, Any]]:
        validate_record(evaluation, "telemetry_slo")
        if evaluation.get("messageType") != "slo_evaluation":
            raise ContractViolation(
                [Violation("TELEMETRY_TYPE", "$.messageType", "SLO evaluation required")]
            )
        slo = self._slos.get(evaluation["sloId"])
        if slo is None:
            raise ContractViolation(
                [Violation("UNKNOWN_SLO", "$.sloId", "SLO identity is not cataloged")]
            )
        if (
            evaluation["catalogVersion"] != self._catalog["catalogVersion"]
            or evaluation["objective"] != slo["objective"]
            or evaluation["comparator"] != slo["comparator"]
        ):
            raise ContractViolation(
                [
                    Violation(
                        "SLO_CATALOG_BINDING_MISMATCH",
                        "$.catalogVersion",
                        "evaluation does not bind the current catalog SLO",
                    )
                ]
            )
        if slo["statistic"] not in {"p50", "p95", "p99"}:
            raise ValueError("the published schemas do not bind this SLO input shape")
        expected_conditions = {
            "slo_warning": (
                f"errorBudgetConsumed>={float(self._catalog['errorBudget']['warningAt'])}"
            ),
            "slo_exhausted": (
                f"errorBudgetConsumed>={float(self._catalog['errorBudget']['criticalAt'])}"
            ),
        }
        if any(
            policy_id not in self._policies or self._policies[policy_id]["when"] != condition
            for policy_id, condition in expected_conditions.items()
        ):
            raise ContractViolation(
                [
                    Violation(
                        "ALERT_POLICY_UNSUPPORTED",
                        "$.policyId",
                        "catalog alert condition differs from the executable threshold policy",
                    )
                ]
            )
        if evaluation["status"] == "insufficient_data":
            if evaluation["actual"] is not None or evaluation["errorBudgetConsumed"] is not None:
                raise ContractViolation(
                    [
                        Violation(
                            "SLO_INSUFFICIENT_DATA_VALUE",
                            "$.actual",
                            "insufficient data forbids actual and error-budget values",
                        )
                    ]
                )
            return []
        budget = evaluation["errorBudgetConsumed"]
        if budget is None:
            raise ContractViolation(
                [
                    Violation(
                        "SLO_ERROR_BUDGET_REQUIRED",
                        "$.errorBudgetConsumed",
                        "a sufficient evaluation requires an error-budget value",
                    )
                ]
            )
        opened = _timestamp(opened_at)
        if opened < _timestamp(evaluation["evaluatedAt"]):
            raise ContractViolation(
                [
                    Violation(
                        "ALERT_TEMPORAL_ORDER",
                        "$.openedAt",
                        "alert cannot open before its SLO evaluation",
                    )
                ]
            )
        triggered = [
            policy_id
            for policy_id, threshold in (
                ("slo_warning", float(self._catalog["errorBudget"]["warningAt"])),
                ("slo_exhausted", float(self._catalog["errorBudget"]["criticalAt"])),
            )
            if float(budget) >= threshold
        ]
        alerts: list[dict[str, Any]] = []
        for policy_id in triggered:
            alert_id = alert_ids.get(policy_id)
            if not alert_id:
                raise ContractViolation(
                    [
                        Violation(
                            "ALERT_ID_REQUIRED",
                            "$.alertId",
                            f"caller must supply an identity for {policy_id}",
                        )
                    ]
                )
            policy = self._policies[policy_id]
            alert = {
                "messageType": "alert_event",
                "schemaVersion": "telemetry-slo/1.0.0",
                "alertId": alert_id,
                "policyId": policy_id,
                "sloEvaluationId": evaluation["evaluationId"],
                "severity": policy["severity"],
                "owner": slo["owner"],
                "route": policy["route"],
                "openedAt": opened_at,
                "state": "open",
                "retentionClass": "audit_7y",
            }
            validate_record(alert, "telemetry_slo")
            alerts.append(alert)
        return alerts
