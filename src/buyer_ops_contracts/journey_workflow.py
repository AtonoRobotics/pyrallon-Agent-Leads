"""Start the published BuyerJourney Temporal workflow after capture. No fake references."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from .structural import validate_record
from .temporal_workflows import start_buyer_journey_workflow


def captured_journey_workflow_input(
    tenant_id: str, journey_id: str, runtime_policy: dict[str, Any]
) -> dict[str, Any]:
    if not tenant_id or not journey_id:
        raise ValueError("tenant_id and journey_id are required")
    payload = {
        "message_type": "workflow_input",
        "schema_version": "ot01-workflow/1.0.0",
        "tenant_id": tenant_id,
        "journey_id": journey_id,
        "workflow_code_version": 1,
        "runtime_policy": runtime_policy,
    }
    validate_record(payload, "temporal")
    return payload


def load_temporal_runtime_policy() -> dict[str, Any] | None:
    raw = os.environ.get("TEMPORAL_RUNTIME_POLICY_JSON", "").strip()
    if not raw:
        return None
    policy = json.loads(raw)
    if not isinstance(policy, dict):
        raise ValueError("TEMPORAL_RUNTIME_POLICY_JSON must be an object")
    return policy


async def start_captured_journey_async(
    *,
    tenant_id: str,
    journey_id: str,
    address: str | None = None,
    task_queue: str | None = None,
    runtime_policy: dict[str, Any] | None = None,
    client: Client | None = None,
) -> dict[str, str] | None:
    """Start BuyerJourneyWorkflow through the shipped starter. Unconfigured Temporal is a no-op."""
    resolved_address = (
        address if address is not None else os.environ.get("TEMPORAL_ADDRESS", "")
    ).strip()
    if client is None and not resolved_address:
        return None
    policy = runtime_policy if runtime_policy is not None else load_temporal_runtime_policy()
    if policy is None:
        return None
    queue = (task_queue or os.environ.get("TEMPORAL_TASK_QUEUE") or "").strip()
    if not queue:
        return None
    payload = captured_journey_workflow_input(tenant_id, journey_id, policy)
    bound = client or await Client.connect(
        resolved_address, namespace=os.environ.get("TEMPORAL_NAMESPACE", "default")
    )
    try:
        handle = await start_buyer_journey_workflow(bound, payload, task_queue=queue)
    except WorkflowAlreadyStartedError:
        return None
    run_id = getattr(handle, "result_run_id", None) or getattr(handle, "first_execution_run_id", None)
    if not handle.id or not run_id:
        return None
    return {"workflow_id": str(handle.id), "run_id": str(run_id)}


def start_captured_journey(**kwargs: Any) -> dict[str, str] | None:
    """Synchronous capture boundary. Does not write a WorkflowReference."""
    return asyncio.run(start_captured_journey_async(**kwargs))
