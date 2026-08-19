"""Temporal worker process. Fails closed if Temporal or DATABASE_URL is absent."""

from __future__ import annotations

import asyncio
import os
from typing import Any, cast

import psycopg
from temporalio.client import Client

from .canonical_repository import CanonicalRepository, Connection
from .operator_projection import OperatorProjection
from .structural import validate_record
from .temporal_workflows import ReconciliationActivities, create_temporal_worker


class PostgresJourneyStateRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def load_current(self, tenant_id: str, journey_id: str) -> dict[str, Any]:
        def load() -> dict[str, Any]:
            connection = psycopg.connect(self._dsn)
            try:
                repo = CanonicalRepository(cast(Connection, connection), tenant_id=tenant_id)
                journey = repo.get(journey_id)
                if journey is None:
                    raise KeyError(journey_id)
                view = OperatorProjection(repo, tenant_id=tenant_id).journey_view(
                    journey_id=journey_id,
                    principal_id=str(journey.get("ownerLicenseHolderId", "unknown")),
                )
                states = view["orthogonal_states"]
                state = {
                    "message_type": "journey_state",
                    "schema_version": "ot01-journey-state/1.0.0",
                    "journey_id": journey_id,
                    "canonical_version": int(journey["version"]),
                    "ingress_state": "identified"
                    if states["journey"] not in {"captured"}
                    else "captured",
                    "contactability_state": states["contactability"]
                    if states["contactability"]
                    in {
                        "unknown",
                        "contactable",
                        "temporarily_unavailable",
                        "suppressed",
                        "invalid",
                    }
                    else "unknown",
                    "acknowledgment_state": states["acknowledgment"]
                    if states["acknowledgment"]
                    in {"not_required", "pending", "sent", "delivered", "failed", "unknown_outcome"}
                    else "pending",
                    "qualification_state": states["qualification"],
                    "consultation_state": states["consultation"]
                    if states["consultation"]
                    in {
                        "not_ready",
                        "ready",
                        "offering",
                        "provider_pending",
                        "booked",
                        "completed",
                        "cancelled",
                        "no_show",
                        "blocked",
                    }
                    else "not_ready",
                    "nurture_state": states["nurture"]
                    if states["nurture"] in {"inactive", "active", "paused", "dormant", "completed"}
                    else "inactive",
                    "blocker_codes": [item["code"] for item in view["blockers"]],
                }
                validate_record(state, "temporal")
                return state
            finally:
                connection.close()

        return await asyncio.to_thread(load)


async def _run() -> None:
    dsn = os.environ.get("BUYER_OPS_DATABASE_DSN") or os.environ.get("DATABASE_URL")
    address = os.environ.get("TEMPORAL_ADDRESS")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    if not dsn or not address:
        raise SystemExit("BUYER_OPS_DATABASE_DSN and TEMPORAL_ADDRESS are required")
    client = await Client.connect(address, namespace=namespace)
    configuration = {
        "message_type": "worker_configuration",
        "schema_version": "buyer-ops-temporal-worker/1.0.0",
        "task_queue": os.environ.get("TEMPORAL_TASK_QUEUE", "buyer-ops-ot01"),
        "max_concurrent_workflow_tasks": 20,
        "max_concurrent_activities": 20,
        "max_cached_workflows": 200,
        "graceful_shutdown_seconds": 15,
    }
    activities = ReconciliationActivities(PostgresJourneyStateRepository(dsn))
    worker = create_temporal_worker(
        client, configuration, activities=[activities.reconcile_journey_state]
    )
    await worker.run()


def main() -> int:
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
