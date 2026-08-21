"""Run the deployed buyer-ops ingress/API/Temporal production smoke journey."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import time
import urllib.request
import uuid
from typing import Any

from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from buyer_ops_contracts.temporal_endpoint import resolve_temporal_address
from buyer_ops_contracts.temporal_workflows import (
    BuyerJourneyWorkflow,
    ConsultationChildWorkflow,
    NurtureChildWorkflow,
    QualificationChildWorkflow,
    RepresentationOnboardingWorkflow,
    buyer_journey_workflow_id,
)


def _request(
    base: str, path: str, *, headers: dict[str, str], body: bytes | None = None
) -> tuple[int, dict[str, Any] | str]:
    request = urllib.request.Request(
        base.rstrip("/") + path, headers=headers, data=body, method="POST" if body else "GET"
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read()
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            return response.status, json.loads(raw.decode())
        return response.status, raw.decode(errors="replace")


async def _run_temporal(tenant_id: str, journey_id: str) -> dict[str, Any]:
    address = os.environ.get("TEMPORAL_ADDRESS")
    namespace = os.environ.get("TEMPORAL_NAMESPACE")
    task_queue = os.environ.get("TEMPORAL_TASK_QUEUE")
    if not address or not namespace or not task_queue:
        raise RuntimeError(
            "TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE, and TEMPORAL_TASK_QUEUE are required"
        )
    client = await Client.connect(resolve_temporal_address(address), namespace=namespace)
    workflow_input = {
        "message_type": "workflow_input",
        "schema_version": "ot01-workflow/1.0.0",
        "tenant_id": tenant_id,
        "journey_id": journey_id,
        "workflow_code_version": 1,
        "runtime_policy": {
            "start_to_close_seconds": 30,
            "initial_retry_seconds": 2,
            "backoff_coefficient": 2,
            "maximum_retry_seconds": 60,
            "maximum_attempts": 3,
        },
    }
    workflow_id = buyer_journey_workflow_id(tenant_id, journey_id)
    try:
        handle = await client.start_workflow(
            BuyerJourneyWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=task_queue,
        )
        started = True
    except WorkflowAlreadyStartedError:
        handle = client.get_workflow_handle(workflow_id)
        started = False
    state = None
    for _ in range(40):
        state = await handle.query(BuyerJourneyWorkflow.current_state)
        if state is not None:
            break
        await asyncio.sleep(0.5)
    if not isinstance(state, dict):
        raise RuntimeError("Temporal workflow did not reconcile canonical state")
    await handle.signal(BuyerJourneyWorkflow.stop)
    result = await handle.result()
    qualification_id = f"qualification:{journey_id}:live-e2e"
    qualification_input = {
        "message_type": "domain_child_input",
        "schema_version": "ot01-domain-child/1.0.0",
        "child_type": "qualification",
        "tenant_id": tenant_id,
        "journey_id": journey_id,
        "parent_workflow_id": workflow_id,
        "workflow_code_version": 1,
        "runtime_policy": workflow_input["runtime_policy"],
    }
    try:
        qualification_handle = await client.start_workflow(
            QualificationChildWorkflow.run,
            qualification_input,
            id=qualification_id,
            task_queue=task_queue,
        )
        qualification_started = True
    except WorkflowAlreadyStartedError:
        qualification_handle = client.get_workflow_handle(qualification_id)
        qualification_started = False
    decision = None
    for _ in range(40):
        decision = await qualification_handle.query(QualificationChildWorkflow.current_decision)
        if isinstance(decision, dict):
            break
        await asyncio.sleep(0.5)
    if not isinstance(decision, dict):
        raise RuntimeError("qualification child did not produce a durable decision")
    await qualification_handle.signal(QualificationChildWorkflow.stop)
    await qualification_handle.result()
    nurture_id = f"nurture:{journey_id}:live-e2e"
    nurture_input = {
        **qualification_input,
        "child_type": "nurture",
    }
    try:
        nurture_handle = await client.start_workflow(
            NurtureChildWorkflow.run,
            nurture_input,
            id=nurture_id,
            task_queue=task_queue,
        )
        nurture_started = True
    except WorkflowAlreadyStartedError:
        nurture_handle = client.get_workflow_handle(nurture_id)
        nurture_started = False
    nurture_decision = None
    for _ in range(40):
        nurture_decision = await nurture_handle.query(NurtureChildWorkflow.current_decision)
        if isinstance(nurture_decision, dict):
            break
        await asyncio.sleep(0.5)
    if not isinstance(nurture_decision, dict):
        raise RuntimeError("nurture child did not produce a durable plan")
    await nurture_handle.signal(NurtureChildWorkflow.stop)
    await nurture_handle.result()
    consultation_id = f"consultation:{journey_id}:live-e2e"
    consultation_input = {
        **qualification_input,
        "child_type": "consultation",
    }
    try:
        consultation_handle = await client.start_workflow(
            ConsultationChildWorkflow.run,
            consultation_input,
            id=consultation_id,
            task_queue=task_queue,
        )
        consultation_started = True
    except WorkflowAlreadyStartedError:
        consultation_handle = client.get_workflow_handle(consultation_id)
        consultation_started = False
    consultation_decision = None
    for _ in range(40):
        consultation_decision = await consultation_handle.query(
            ConsultationChildWorkflow.current_decision
        )
        if isinstance(consultation_decision, dict):
            break
        await asyncio.sleep(0.5)
    if not isinstance(consultation_decision, dict):
        raise RuntimeError("consultation child did not produce a durable decision")
    await consultation_handle.signal(ConsultationChildWorkflow.stop)
    await consultation_handle.result()
    representation_id = f"representation:{journey_id}:live-e2e"
    representation_input = {
        "message_type": "representation_workflow_input",
        "schema_version": "representation-operation/1.0.0",
        "tenant_id": tenant_id,
        "journey_id": journey_id,
        "runtime_policy": workflow_input["runtime_policy"],
    }
    try:
        representation_handle = await client.start_workflow(
            RepresentationOnboardingWorkflow.run,
            representation_input,
            id=representation_id,
            task_queue=task_queue,
        )
        representation_started = True
    except WorkflowAlreadyStartedError:
        representation_handle = client.get_workflow_handle(representation_id)
        representation_started = False
    representation_decision = None
    for _ in range(40):
        representation_decision = await representation_handle.query(
            RepresentationOnboardingWorkflow.current_decision
        )
        if isinstance(representation_decision, dict):
            break
        await asyncio.sleep(0.5)
    if not isinstance(representation_decision, dict):
        raise RuntimeError("representation child did not produce a durable decision")
    await representation_handle.signal(RepresentationOnboardingWorkflow.stop)
    await representation_handle.result()
    return {
        "workflowId": workflow_id,
        "started": started,
        "state": state,
        "result": result,
        "qualification": {
            "workflowId": qualification_id,
            "started": qualification_started,
            "decision": decision,
        },
        "nurture": {
            "workflowId": nurture_id,
            "started": nurture_started,
            "decision": nurture_decision,
        },
        "consultation": {
            "workflowId": consultation_id,
            "started": consultation_started,
            "decision": consultation_decision,
        },
        "representation": {
            "workflowId": representation_id,
            "started": representation_started,
            "decision": representation_decision,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8090")
    args = parser.parse_args()
    token = os.environ.get("BUYER_OPS_CONTROL_TOKEN", "")
    tenant_id = os.environ.get("BUYER_OPS_E2E_TENANT", "tenant-1")
    actor_id = os.environ.get("BUYER_OPS_E2E_ACTOR", "live-e2e-runner")
    webhook_secret = os.environ.get("BUYER_OPS_INGRESS_WEBHOOK_SECRET", "")
    if not token or len(webhook_secret) < 16:
        raise SystemExit(
            "BUYER_OPS_CONTROL_TOKEN and BUYER_OPS_INGRESS_WEBHOOK_SECRET are required"
        )
    headers = {
        "x-buyer-ops-token": token,
        "x-buyer-ops-tenant": tenant_id,
        "x-buyer-ops-actor": actor_id,
    }
    health_status, _ = _request(args.base, "/health", headers=headers)
    workspace_status, workspace = _request(args.base, "/v1/workspace", headers=headers)
    event_id = f"live-production-e2e-{uuid.uuid4().hex}"
    body = json.dumps(
        {
            "event": {"id": event_id},
            "lead": {"email": f"{event_id}@example.test", "name": "Live E2E"},
            "thread": {"id": event_id},
        },
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    ingress_headers = {
        "content-type": "application/json",
        "x-provider-signature": f"sha256={signature}",
    }
    first_status, first = _request(
        args.base, "/v1/ingress/webhook/primary-form", headers=ingress_headers, body=body
    )
    replay_status, replay = _request(
        args.base, "/v1/ingress/webhook/primary-form", headers=ingress_headers, body=body
    )
    if not isinstance(first, dict) or not isinstance(replay, dict) or not replay.get("duplicate"):
        raise RuntimeError("signed ingress replay was not deduplicated")
    temporal = asyncio.run(_run_temporal(tenant_id, str(first["journey_id"])))
    result = {
        "schemaVersion": "buyer-ops-live-e2e/1.0.0",
        "passed": health_status == 200
        and workspace_status == 200
        and first_status == 200
        and replay_status == 200
        and isinstance(temporal.get("qualification", {}).get("decision"), dict)
        and isinstance(temporal.get("nurture", {}).get("decision"), dict)
        and isinstance(temporal.get("consultation", {}).get("decision"), dict)
        and isinstance(temporal.get("representation", {}).get("decision"), dict),
        "healthStatus": health_status,
        "workspaceStatus": workspace_status,
        "workspaceJourneyCount": len(workspace.get("journeys", []))
        if isinstance(workspace, dict)
        else None,
        "ingressStatus": first_status,
        "replayStatus": replay_status,
        "duplicate": replay.get("duplicate"),
        "journeyId": first.get("journey_id"),
        "temporal": temporal,
        "completedAt": time.time(),
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
