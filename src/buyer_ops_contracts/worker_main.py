"""Temporal worker process with owner-supplied runtime and journey-state semantics."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, cast

import psycopg
from temporalio.client import Client

from .canonical_repository import CanonicalRepository, Connection
from .structural import validate_record
from .temporal_workflows import ReconciliationActivities, create_temporal_worker


class PostgresJourneyStateRepository:
    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("database DSN is required")
        self._dsn = dsn

    async def load_current(self, tenant_id: str, journey_id: str) -> dict[str, Any]:
        def load() -> dict[str, Any]:
            connection = psycopg.connect(self._dsn)
            try:
                repository = CanonicalRepository(cast(Connection, connection), tenant_id=tenant_id)
                journey = repository.get(journey_id)
                if journey is None or journey.get("recordType") != "BuyerJourney":
                    raise KeyError(journey_id)
                raise RuntimeError("governed OT-01 JourneyState derivation is unavailable")
            finally:
                connection.close()

        return await asyncio.to_thread(load)


def validate_compiled_journey_state(
    state: dict[str, Any], *, journey_id: str, canonical_version: int
) -> dict[str, Any]:
    """Admit only a structurally valid projection bound to the current journey version."""
    validate_record(state, "temporal")
    if (
        state.get("message_type") != "journey_state"
        or state.get("journey_id") != journey_id
        or int(state.get("canonical_version", 0)) != canonical_version
    ):
        raise ValueError("JourneyState does not bind the current canonical journey")
    return state


def load_worker_configuration(raw: str | None = None) -> dict[str, Any]:
    """Load the complete published WorkerConfiguration without implementation defaults."""
    encoded = raw if raw is not None else os.environ.get("TEMPORAL_WORKER_CONFIGURATION_JSON", "")
    if not encoded.strip():
        raise ValueError("TEMPORAL_WORKER_CONFIGURATION_JSON is required")
    configuration = json.loads(encoded)
    if not isinstance(configuration, dict):
        raise ValueError("Temporal worker configuration must be an object")
    validate_record(configuration, "temporal")
    if configuration.get("message_type") != "worker_configuration":
        raise ValueError("Temporal worker configuration has the wrong message type")
    return cast(dict[str, Any], configuration)


async def _run() -> None:
    dsn = os.environ.get("BUYER_OPS_DATABASE_DSN") or os.environ.get("DATABASE_URL")
    address = os.environ.get("TEMPORAL_ADDRESS")
    namespace = os.environ.get("TEMPORAL_NAMESPACE")
    if not dsn or not address or not namespace:
        raise SystemExit(
            "BUYER_OPS_DATABASE_DSN, TEMPORAL_ADDRESS, and TEMPORAL_NAMESPACE are required"
        )
    raise SystemExit("governed OT-01 JourneyState derivation is unavailable")
    configuration = load_worker_configuration()
    client = await Client.connect(address, namespace=namespace)
    activities = ReconciliationActivities(PostgresJourneyStateRepository(dsn))
    worker = create_temporal_worker(
        client,
        configuration,
        activities=[activities.reconcile_journey_state],
    )
    await worker.run()


def main() -> int:
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
