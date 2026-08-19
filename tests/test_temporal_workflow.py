import asyncio
import os
from pathlib import Path

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError, CancelledError, WorkflowAlreadyStartedError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from buyer_ops_contracts.journey_workflow import start_captured_journey_async
from buyer_ops_contracts.temporal_workflows import (
    BuyerJourneyWorkflow,
    CompensationActivities,
    ConnectorReconciliationWorkflow,
    ConsultationChildWorkflow,
    NurtureChildWorkflow,
    QualificationChildWorkflow,
    ReconciliationActivities,
    buyer_journey_workflow_runner,
    create_temporal_worker,
    start_buyer_journey_workflow,
)


def _compensation_command() -> dict:
    return {
        "message_type": "compensation_command",
        "schema_version": "ot01-compensation/1.1.0",
        "command_id": "compensation-1",
        "tenant_id": "tenant-1",
        "journey_id": "journey-1",
        "workflow_id": "workflow-1",
        "effect_attempt_id": "effect-attempt-1",
        "expected_effect_version": 2,
        "eligible_attempt_states": ["confirmed"],
        "compensation_action": "cancel",
        "payload_digest": "sha256:" + "a" * 64,
        "habitat_permit_digest": "sha256:" + "b" * 64,
        "authorization_id": "authorization-1",
        "idempotency_key": "compensation-key-1",
        "issued_at": "2026-08-19T12:00:00Z",
        "expires_at": "2026-08-19T12:05:00Z",
    }


def test_compensation_activity_preserves_governed_command_and_result_bindings() -> None:
    class Executor:
        async def execute_compensation(self, command: dict) -> dict:
            assert command == _compensation_command()
            return {
                "message_type": "compensation_result",
                "schema_version": "ot01-compensation/1.1.0",
                "command_id": command["command_id"],
                "effect_attempt_id": command["effect_attempt_id"],
                "state": "confirmed",
                "attempt": 1,
                "provider_receipt_id": "receipt-1",
                "decided_at": "2026-08-19T12:01:00Z",
                "evidence_ids": ["evidence-1"],
            }

    result = asyncio.run(
        CompensationActivities(Executor()).execute_compensation(_compensation_command())
    )
    assert result["state"] == "confirmed"
    assert result["provider_receipt_id"] == "receipt-1"


def test_compensation_activity_rejects_a_result_for_another_effect() -> None:
    class Executor:
        async def execute_compensation(self, command: dict) -> dict:
            return {
                "message_type": "compensation_result",
                "schema_version": "ot01-compensation/1.1.0",
                "command_id": command["command_id"],
                "effect_attempt_id": "effect-attempt-other",
                "state": "ineligible",
                "attempt": 1,
                "decided_at": "2026-08-19T12:01:00Z",
                "evidence_ids": ["evidence-1"],
            }

    with pytest.raises(ApplicationError) as raised:
        asyncio.run(
            CompensationActivities(Executor()).execute_compensation(_compensation_command())
        )
    assert raised.value.type == "compensation_result_conflict"


def _input() -> dict:
    return {
        "message_type": "workflow_input",
        "schema_version": "ot01-workflow/1.0.0",
        "tenant_id": "tenant-1",
        "journey_id": "journey-1",
        "workflow_code_version": 1,
        "runtime_policy": {
            "start_to_close_seconds": 30,
            "initial_retry_seconds": 1,
            "backoff_coefficient": 2,
            "maximum_retry_seconds": 10,
            "maximum_attempts": 5,
        },
    }


def _state(version: int, ingress_state: str) -> dict:
    return {
        "message_type": "journey_state",
        "schema_version": "ot01-journey-state/1.0.0",
        "journey_id": "journey-1",
        "canonical_version": version,
        "ingress_state": ingress_state,
        "contactability_state": "contactable",
        "acknowledgment_state": "pending",
        "qualification_state": "not_started",
        "consultation_state": "not_ready",
        "nurture_state": "inactive",
        "blocker_codes": [],
    }


async def _wait_for_version(handle, version: int) -> dict:
    for _ in range(100):
        state = await handle.query(BuyerJourneyWorkflow.current_state)
        if state is not None and state["canonical_version"] == version:
            return state
        await asyncio.sleep(0.01)
    raise AssertionError(f"workflow did not reconcile canonical version {version}")


def _test_server_options() -> dict:
    cached_server = Path("/tmp/temporal-test-server-sdk-python-1.30.0")
    existing_server = os.environ.get("TEMPORAL_TEST_SERVER_PATH")
    if existing_server is None and cached_server.is_file():
        existing_server = str(cached_server)
    return {
        "test_server_existing_path": existing_server,
        "test_server_download_version": "1.30.0",
    }


def _worker_configuration(task_queue: str) -> dict:
    return {
        "message_type": "worker_configuration",
        "schema_version": "buyer-ops-temporal-worker/1.0.0",
        "task_queue": task_queue,
        "max_concurrent_workflow_tasks": 20,
        "max_concurrent_activities": 20,
        "max_cached_workflows": 100,
        "graceful_shutdown_seconds": 10,
    }


def test_reconciliation_activity_reads_and_validates_canonical_state() -> None:
    class Repository:
        async def load_current(self, tenant_id: str, journey_id: str) -> dict:
            assert (tenant_id, journey_id) == ("tenant-1", "journey-1")
            return _state(3, "identified")

    result = asyncio.run(
        ReconciliationActivities(Repository()).reconcile_journey_state(
            {"tenant_id": "tenant-1", "journey_id": "journey-1"}
        )
    )
    assert result["canonical_version"] == 3


def test_capture_starts_buyer_journey_workflow_when_temporal_is_configured() -> None:
    async def scenario() -> None:
        @activity.defn(name="reconcile_journey_state")
        async def reconcile(request: dict) -> dict:
            return _state(1, "captured")

        async with (
            await WorkflowEnvironment.start_time_skipping(**_test_server_options()) as environment,
            create_temporal_worker(
                environment.client,
                _worker_configuration("capture-journey-test"),
                activities=[reconcile],
            ),
        ):
            started = await start_captured_journey_async(
                tenant_id="tenant-1",
                journey_id="journey-1",
                address="configured",
                task_queue="capture-journey-test",
                runtime_policy=_input()["runtime_policy"],
                client=environment.client,
            )
            assert started is not None
            assert started["workflow_id"]
            assert started["run_id"]
            handle = environment.client.get_workflow_handle(started["workflow_id"])
            state = await _wait_for_version(handle, 1)
            assert state["journey_id"] == "journey-1"

    asyncio.run(scenario())


def test_buyer_journey_workflow_reconciles_current_canonical_state() -> None:
    async def scenario() -> None:
        snapshots = [_state(1, "captured"), _state(2, "identified")]

        @activity.defn(name="reconcile_journey_state")
        async def reconcile(request: dict) -> dict:
            return snapshots.pop(0)

        async with (
            await WorkflowEnvironment.start_time_skipping(**_test_server_options()) as environment,
            create_temporal_worker(
                environment.client,
                _worker_configuration("buyer-journey-test"),
                activities=[reconcile],
            ),
        ):
            handle = await start_buyer_journey_workflow(
                environment.client,
                _input(),
                task_queue="buyer-journey-test",
            )
            with pytest.raises(WorkflowAlreadyStartedError):
                await start_buyer_journey_workflow(
                    environment.client,
                    _input(),
                    task_queue="buyer-journey-test",
                )
            initial = await _wait_for_version(handle, 1)
            assert initial["canonical_version"] == 1

            await handle.signal(
                BuyerJourneyWorkflow.canonical_changed,
                {
                    "message_type": "canonical_changed",
                    "schema_version": "ot01-canonical-change/1.0.0",
                    "tenant_id": "tenant-1",
                    "journey_id": "journey-1",
                    "event_id": "event-2",
                    "observed_canonical_version": 2,
                },
            )
            reconciled = await _wait_for_version(handle, 2)
            assert reconciled["canonical_version"] == 2
            assert reconciled["ingress_state"] == "identified"
            await handle.signal(
                BuyerJourneyWorkflow.canonical_changed,
                {
                    "message_type": "canonical_changed",
                    "schema_version": "ot01-canonical-change/1.0.0",
                    "tenant_id": "tenant-1",
                    "journey_id": "journey-1",
                    "event_id": "event-2",
                    "observed_canonical_version": 2,
                },
            )
            await handle.signal(
                BuyerJourneyWorkflow.canonical_changed,
                {
                    "message_type": "canonical_changed",
                    "schema_version": "ot01-canonical-change/1.0.0",
                    "tenant_id": "tenant-1",
                    "journey_id": "journey-1",
                    "event_id": "event-late-1",
                    "observed_canonical_version": 1,
                },
            )
            await handle.signal(
                BuyerJourneyWorkflow.canonical_changed,
                {
                    "message_type": "canonical_changed",
                    "schema_version": "ot01-canonical-change/1.0.0",
                    "tenant_id": "tenant-other",
                    "journey_id": "journey-1",
                    "event_id": "event-cross-tenant",
                    "observed_canonical_version": 3,
                },
            )
            await handle.signal(BuyerJourneyWorkflow.stop)
            assert await handle.result() == reconciled
            history = await handle.fetch_history()
            replay = await Replayer(
                workflows=[BuyerJourneyWorkflow],
                workflow_runner=buyer_journey_workflow_runner(),
            ).replay_workflow(history)
            assert replay.replay_failure is None

    asyncio.run(scenario())


def test_buyer_journey_workflow_survives_worker_replacement() -> None:
    async def scenario() -> None:
        snapshots = [_state(1, "captured"), _state(2, "identified")]

        @activity.defn(name="reconcile_journey_state")
        async def reconcile(request: dict) -> dict:
            return snapshots.pop(0)

        async with await WorkflowEnvironment.start_time_skipping(
            **_test_server_options()
        ) as environment:
            async with Worker(
                environment.client,
                task_queue="buyer-journey-restart-test",
                workflows=[BuyerJourneyWorkflow],
                activities=[reconcile],
                workflow_runner=buyer_journey_workflow_runner(),
                max_cached_workflows=0,
            ):
                handle = await start_buyer_journey_workflow(
                    environment.client,
                    _input(),
                    task_queue="buyer-journey-restart-test",
                )
                await _wait_for_version(handle, 1)

            async with Worker(
                environment.client,
                task_queue="buyer-journey-restart-test",
                workflows=[BuyerJourneyWorkflow],
                activities=[reconcile],
                workflow_runner=buyer_journey_workflow_runner(),
                max_cached_workflows=0,
            ):
                await handle.signal(
                    BuyerJourneyWorkflow.canonical_changed,
                    {
                        "message_type": "canonical_changed",
                        "schema_version": "ot01-canonical-change/1.0.0",
                        "tenant_id": "tenant-1",
                        "journey_id": "journey-1",
                        "event_id": "event-after-worker-replacement",
                        "observed_canonical_version": 2,
                    },
                )
                state = await _wait_for_version(handle, 2)
                await handle.signal(BuyerJourneyWorkflow.stop)
                assert await handle.result() == state

    asyncio.run(scenario())


def test_connector_reconciliation_waits_for_terminal_canonical_outcome() -> None:
    async def scenario() -> None:
        outcomes = [
            {
                "message_type": "effect_reconciliation_state",
                "schema_version": "ot01-effect-reconciliation-state/1.0.0",
                "effect_attempt_id": "effect-attempt-1",
                "attempt_state": "unknown_outcome",
            },
            {
                "message_type": "effect_reconciliation_state",
                "schema_version": "ot01-effect-reconciliation-state/1.0.0",
                "effect_attempt_id": "effect-attempt-1",
                "attempt_state": "reconciled_succeeded",
                "provider_receipt_id": "receipt-1",
            },
        ]

        @activity.defn(name="reconcile_unknown_effect")
        async def reconcile(request: dict) -> dict:
            assert request["effect_attempt_id"] == "effect-attempt-1"
            return outcomes.pop(0)

        async with (
            await WorkflowEnvironment.start_time_skipping(**_test_server_options()) as environment,
            Worker(
                environment.client,
                task_queue="connector-reconciliation-test",
                workflows=[ConnectorReconciliationWorkflow],
                activities=[reconcile],
                workflow_runner=buyer_journey_workflow_runner(),
            ),
        ):
            handle = await environment.client.start_workflow(
                ConnectorReconciliationWorkflow.run,
                {
                    "message_type": "connector_reconciliation_input",
                    "schema_version": "ot01-connector-reconciliation/1.0.0",
                    "tenant_id": "tenant-1",
                    "journey_id": "journey-1",
                    "parent_workflow_id": "parent-1",
                    "effect_attempt_id": "effect-attempt-1",
                    "workflow_code_version": 1,
                    "runtime_policy": _input()["runtime_policy"],
                    "reconciliation_interval_seconds": 60,
                },
                id="connector-reconciliation:effect-attempt-1",
                task_queue="connector-reconciliation-test",
            )
            result = await handle.result()
            assert result["attempt_state"] == "reconciled_succeeded"
            assert outcomes == []
            replay = await Replayer(
                workflows=[ConnectorReconciliationWorkflow],
                workflow_runner=buyer_journey_workflow_runner(),
            ).replay_workflow(await handle.fetch_history())
            assert replay.replay_failure is None

    asyncio.run(scenario())


def test_domain_child_skeletons_hold_only_reconciled_canonical_views() -> None:
    async def scenario() -> None:
        @activity.defn(name="reconcile_journey_state")
        async def reconcile(request: dict) -> dict:
            return _state(1, "identified")

        workflows = [
            QualificationChildWorkflow,
            NurtureChildWorkflow,
            ConsultationChildWorkflow,
        ]
        async with (
            await WorkflowEnvironment.start_time_skipping(**_test_server_options()) as environment,
            Worker(
                environment.client,
                task_queue="domain-child-test",
                workflows=workflows,
                activities=[reconcile],
                workflow_runner=buyer_journey_workflow_runner(),
            ),
        ):
            histories = []
            for child_type, workflow_class in zip(
                ("qualification", "nurture", "consultation"), workflows, strict=True
            ):
                handle = await environment.client.start_workflow(
                    workflow_class.run,
                    {
                        "message_type": "domain_child_input",
                        "schema_version": "ot01-domain-child/1.0.0",
                        "child_type": child_type,
                        "tenant_id": "tenant-1",
                        "journey_id": "journey-1",
                        "parent_workflow_id": "parent-1",
                        "workflow_code_version": 1,
                        "runtime_policy": _input()["runtime_policy"],
                    },
                    id=f"{child_type}:journey-1",
                    task_queue="domain-child-test",
                )
                for _ in range(100):
                    state = await handle.query("current_state")
                    if state is not None:
                        break
                    await asyncio.sleep(0.01)
                else:
                    raise AssertionError(f"{child_type} child did not reconcile")
                assert state["canonical_version"] == 1
                await handle.signal("stop")
                assert await handle.result() == state
                histories.append(await handle.fetch_history())

            replayer = Replayer(
                workflows=workflows,
                workflow_runner=buyer_journey_workflow_runner(),
            )
            for history in histories:
                replay = await replayer.replay_workflow(history)
                assert replay.replay_failure is None

    asyncio.run(scenario())


def test_cancelling_unknown_outcome_never_fabricates_completion_or_compensation() -> None:
    async def scenario() -> None:
        called = asyncio.Event()
        call_count = 0

        @activity.defn(name="reconcile_unknown_effect")
        async def reconcile(request: dict) -> dict:
            nonlocal call_count
            call_count += 1
            called.set()
            return {
                "message_type": "effect_reconciliation_state",
                "schema_version": "ot01-effect-reconciliation-state/1.0.0",
                "effect_attempt_id": "effect-attempt-cancelled",
                "attempt_state": "unknown_outcome",
            }

        async with (
            await WorkflowEnvironment.start_time_skipping(**_test_server_options()) as environment,
            Worker(
                environment.client,
                task_queue="connector-cancellation-test",
                workflows=[ConnectorReconciliationWorkflow],
                activities=[reconcile],
                workflow_runner=buyer_journey_workflow_runner(),
            ),
        ):
            workflow_input = {
                "message_type": "connector_reconciliation_input",
                "schema_version": "ot01-connector-reconciliation/1.0.0",
                "tenant_id": "tenant-1",
                "journey_id": "journey-1",
                "parent_workflow_id": "parent-1",
                "effect_attempt_id": "effect-attempt-cancelled",
                "workflow_code_version": 1,
                "runtime_policy": _input()["runtime_policy"],
                "reconciliation_interval_seconds": 3600,
            }
            handle = await environment.client.start_workflow(
                ConnectorReconciliationWorkflow.run,
                workflow_input,
                id="connector-reconciliation:effect-attempt-cancelled",
                task_queue="connector-cancellation-test",
            )
            await asyncio.wait_for(called.wait(), timeout=2)
            await handle.cancel()
            with pytest.raises(WorkflowFailureError) as raised:
                await handle.result()
            assert isinstance(raised.value.cause, CancelledError)
            assert call_count == 1
            replay = await Replayer(
                workflows=[ConnectorReconciliationWorkflow],
                workflow_runner=buyer_journey_workflow_runner(),
            ).replay_workflow(await handle.fetch_history())
            assert replay.replay_failure is None

    asyncio.run(scenario())


def test_reconciliation_activity_retries_with_explicit_policy() -> None:
    async def scenario() -> None:
        attempts = 0

        @activity.defn(name="reconcile_journey_state")
        async def reconcile(request: dict) -> dict:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError("transient canonical service outage")
            return _state(1, "captured")

        async with (
            await WorkflowEnvironment.start_time_skipping(**_test_server_options()) as environment,
            Worker(
                environment.client,
                task_queue="reconciliation-retry-test",
                workflows=[BuyerJourneyWorkflow],
                activities=[reconcile],
                workflow_runner=buyer_journey_workflow_runner(),
            ),
        ):
            handle = await start_buyer_journey_workflow(
                environment.client,
                _input(),
                task_queue="reconciliation-retry-test",
            )
            await environment.sleep(4)
            state = await _wait_for_version(handle, 1)
            assert attempts == 3
            await handle.signal(BuyerJourneyWorkflow.stop)
            assert await handle.result() == state

    asyncio.run(scenario())
