"""Catalog-only telemetry observations. Prohibited dimensions fail closed."""

from __future__ import annotations

import hashlib
import json
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
    root = files("buyer_ops_contracts")
    try:
        return cast(dict[str, Any], json.loads(root.joinpath("telemetry_catalog.json").read_text()))
    except FileNotFoundError:
        from pathlib import Path

        fallback = Path(__file__).resolve().parents[2] / "TELEMETRY-SLO-CATALOG.json"
        return cast(dict[str, Any], json.loads(fallback.read_text()))


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
        self._metric_ids = {metric["id"] for metric in self._catalog["metrics"]}

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
        if metric_id not in self._metric_ids:
            raise ContractViolation(
                [Violation("UNKNOWN_METRIC", "$.metricId", f"{metric_id} is not in the catalog")]
            )
        if observation["unit"] == "ratio":
            raise ContractViolation(
                [
                    Violation(
                        "RATIO_EVENT_SETS_REQUIRED",
                        "$.unit",
                        "ratio observations must be constructed by RatioTelemetryRecorder",
                    )
                ]
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
        if len(dimensions) > self._catalog["dimensionPolicy"]["maximumSeriesPerMetric"]:
            raise ContractViolation(
                [Violation("SERIES_LIMIT", "$.dimensions", "series cardinality exceeded")]
            )
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
