"""PKT-04 durable workflow ownership without canonical or effect authority."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any, Protocol, cast

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import (
    RetryPolicy,
    VersioningBehavior,
    WorkflowIDConflictPolicy,
    WorkflowIDReusePolicy,
)
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from .errors import ContractViolation
from .structural import validate_record


class JourneyStateRepository(Protocol):
    async def load_current(self, tenant_id: str, journey_id: str) -> dict[str, Any]: ...


class EffectReconciliationRepository(Protocol):
    async def reconcile_unknown_effect(self, request: dict[str, Any]) -> dict[str, Any]: ...


class QualificationEvaluationRepository(Protocol):
    async def evaluate_qualification(self, request: dict[str, Any]) -> dict[str, Any]: ...


class NurtureEvaluationRepository(Protocol):
    async def evaluate_nurture(self, request: dict[str, Any]) -> dict[str, Any]: ...


class TransactionEvaluationRepository(Protocol):
    async def evaluate_transaction(self, request: dict[str, Any]) -> dict[str, Any]: ...


class ConsultationEvaluationRepository(Protocol):
    async def evaluate_consultation(self, request: dict[str, Any]) -> dict[str, Any]: ...


class RepresentationEvaluationRepository(Protocol):
    async def evaluate_representation(self, request: dict[str, Any]) -> dict[str, Any]: ...


class CompensationExecutor(Protocol):
    async def execute_compensation(self, command: dict[str, Any]) -> dict[str, Any]: ...


class CompensationActivities:
    """Validated Temporal boundary for a separately authorized compensation executor."""

    def __init__(self, executor: CompensationExecutor) -> None:
        self._executor = executor

    @activity.defn(name="execute_compensation")
    async def execute_compensation(self, command: dict[str, Any]) -> dict[str, Any]:
        validate_record(command, "temporal")
        if command.get("message_type") != "compensation_command":
            raise ApplicationError(
                "compensation activity requires a compensation command",
                type="compensation_command_invalid",
                non_retryable=True,
            )
        result = await self._executor.execute_compensation(command)
        validate_record(result, "temporal")
        if (
            result.get("message_type") != "compensation_result"
            or result.get("command_id") != command["command_id"]
            or result.get("effect_attempt_id") != command["effect_attempt_id"]
        ):
            raise ApplicationError(
                "compensation result does not match its governed command",
                type="compensation_result_conflict",
                non_retryable=True,
            )
        return result


class ReconciliationActivities:
    """Activity boundary that reads canonical PostgreSQL state without owning it."""

    def __init__(
        self,
        repository: JourneyStateRepository,
        effect_repository: EffectReconciliationRepository | None = None,
    ) -> None:
        self._repository = repository
        self._effect_repository = effect_repository

    @activity.defn(name="reconcile_journey_state")
    async def reconcile_journey_state(self, request: dict[str, Any]) -> dict[str, Any]:
        if (
            set(request) != {"tenant_id", "journey_id"}
            or not isinstance(request.get("tenant_id"), str)
            or not request["tenant_id"]
            or not isinstance(request.get("journey_id"), str)
            or not request["journey_id"]
        ):
            raise ApplicationError(
                "reconciliation request failed tenant/journey scope admission",
                type="reconciliation_request_invalid",
                non_retryable=True,
            )
        state = await self._repository.load_current(request["tenant_id"], request["journey_id"])
        validate_record(state, "temporal")
        if (
            state.get("message_type") != "journey_state"
            or state.get("journey_id") != request["journey_id"]
        ):
            raise ApplicationError(
                "canonical repository returned the wrong journey",
                type="canonical_state_conflict",
                non_retryable=True,
            )
        return state

    @activity.defn(name="reconcile_unknown_effect")
    async def reconcile_unknown_effect(self, request: dict[str, Any]) -> dict[str, Any]:
        if set(request) != {"tenant_id", "journey_id", "effect_attempt_id"} or any(
            not isinstance(request.get(field), str) or not request[field] for field in request
        ):
            raise ApplicationError(
                "effect reconciliation request failed tenant scope admission",
                type="reconciliation_request_invalid",
                non_retryable=True,
            )
        if self._effect_repository is None:
            raise ApplicationError(
                "production effect reconciliation repository is not configured",
                type="reconciliation_unavailable",
                non_retryable=True,
            )
        result = await self._effect_repository.reconcile_unknown_effect(request)
        if not isinstance(result, dict):
            raise ApplicationError(
                "effect reconciliation returned a non-object",
                type="canonical_state_invalid",
                non_retryable=True,
            )
        return result


class QualificationActivities:
    """Activity boundary for policy-owned progressive qualification evaluation."""

    def __init__(self, repository: QualificationEvaluationRepository) -> None:
        self._repository = repository

    @activity.defn(name="evaluate_qualification")
    async def evaluate_qualification(self, request: dict[str, Any]) -> dict[str, Any]:
        if (
            set(request) != {"tenant_id", "journey_id"}
            or not isinstance(request.get("tenant_id"), str)
            or not request["tenant_id"]
            or not isinstance(request.get("journey_id"), str)
            or not request["journey_id"]
        ):
            raise ApplicationError(
                "qualification evaluation request failed scope admission",
                type="qualification_request_invalid",
                non_retryable=True,
            )
        result = await self._repository.evaluate_qualification(request)
        if not isinstance(result, dict) or set(result) != {
            "input_set",
            "next_question",
            "readiness",
        }:
            raise ApplicationError(
                "qualification evaluation returned an invalid result",
                type="qualification_result_invalid",
                non_retryable=True,
            )
        try:
            validate_record(result["input_set"], "qualification_readiness")
            validate_record(result["next_question"], "qualification_readiness")
            validate_record(result["readiness"], "qualification_readiness")
        except (KeyError, TypeError, ContractViolation) as exc:
            raise ApplicationError(
                "qualification evaluation returned malformed contract records",
                type="qualification_result_invalid",
                non_retryable=True,
            ) from exc
        if (
            any(
                item.get("tenantId") != request["tenant_id"]
                for item in result.values()
                if isinstance(item, dict)
            )
            or result["readiness"].get("journeyRef", {}).get("recordId") != request["journey_id"]
        ):
            raise ApplicationError(
                "qualification evaluation crossed its workflow scope",
                type="qualification_result_conflict",
                non_retryable=True,
            )
        return result


class NurtureActivities:
    """Activity boundary for policy-owned contextual nurture planning."""

    def __init__(self, repository: NurtureEvaluationRepository) -> None:
        self._repository = repository

    @activity.defn(name="evaluate_nurture")
    async def evaluate_nurture(self, request: dict[str, Any]) -> dict[str, Any]:
        if (
            set(request) != {"tenant_id", "journey_id"}
            or not isinstance(request.get("tenant_id"), str)
            or not request["tenant_id"]
            or not isinstance(request.get("journey_id"), str)
            or not request["journey_id"]
        ):
            raise ApplicationError(
                "nurture evaluation request failed scope admission",
                type="nurture_request_invalid",
                non_retryable=True,
            )
        result = await self._repository.evaluate_nurture(request)
        if not isinstance(result, dict):
            raise ApplicationError(
                "nurture evaluation returned a non-object",
                type="nurture_result_invalid",
                non_retryable=True,
            )
        try:
            validate_record(result, "nurture_plan")
        except ContractViolation as exc:
            raise ApplicationError(
                "nurture evaluation returned a malformed plan",
                type="nurture_result_invalid",
                non_retryable=True,
            ) from exc
        if (
            result.get("tenantId") != request["tenant_id"]
            or result.get("journeyId") != request["journey_id"]
        ):
            raise ApplicationError(
                "nurture evaluation crossed its workflow scope",
                type="nurture_result_conflict",
                non_retryable=True,
            )
        return result


class TransactionActivities:
    """Activity boundary for confirmed-date transaction coordination."""

    def __init__(self, repository: TransactionEvaluationRepository) -> None:
        self._repository = repository

    @activity.defn(name="evaluate_transaction")
    async def evaluate_transaction(self, request: dict[str, Any]) -> dict[str, Any]:
        if set(request) != {"tenant_id", "journey_id", "transaction_id"} or any(
            not isinstance(request.get(field), str) or not request[field] for field in request
        ):
            raise ApplicationError(
                "transaction evaluation request failed scope admission",
                type="transaction_request_invalid",
                non_retryable=True,
            )
        result = await self._repository.evaluate_transaction(request)
        try:
            validate_record(result, "transaction_coordination")
        except ContractViolation as exc:
            raise ApplicationError(
                "transaction evaluation returned a malformed plan",
                type="transaction_result_invalid",
                non_retryable=True,
            ) from exc
        if (
            result.get("journeyId") != request["journey_id"]
            or result.get("transactionId") != request["transaction_id"]
        ):
            raise ApplicationError(
                "transaction evaluation crossed its workflow scope",
                type="transaction_result_conflict",
                non_retryable=True,
            )
        return result


class ConsultationActivities:
    """Activity boundary for consultation criteria, pre-meeting, and briefing state."""

    def __init__(self, repository: ConsultationEvaluationRepository) -> None:
        self._repository = repository

    @activity.defn(name="evaluate_consultation")
    async def evaluate_consultation(self, request: dict[str, Any]) -> dict[str, Any]:
        if set(request) != {"tenant_id", "journey_id"} or any(
            not isinstance(request.get(field), str) or not request[field] for field in request
        ):
            raise ApplicationError(
                "consultation evaluation request failed scope admission",
                type="consultation_request_invalid",
                non_retryable=True,
            )
        result = await self._repository.evaluate_consultation(request)
        try:
            validate_record(result, "consultation_operation")
        except ContractViolation as exc:
            raise ApplicationError(
                "consultation evaluation returned a malformed decision",
                type="consultation_result_invalid",
                non_retryable=True,
            ) from exc
        if (
            result.get("tenantId") != request["tenant_id"]
            or result.get("journeyId") != request["journey_id"]
        ):
            raise ApplicationError(
                "consultation evaluation crossed its workflow scope",
                type="consultation_result_conflict",
                non_retryable=True,
            )
        return result


class RepresentationActivities:
    """Activity boundary for agent-approved representation onboarding state."""

    def __init__(self, repository: RepresentationEvaluationRepository) -> None:
        self._repository = repository

    @activity.defn(name="evaluate_representation")
    async def evaluate_representation(self, request: dict[str, Any]) -> dict[str, Any]:
        if set(request) != {"tenant_id", "journey_id"} or any(
            not isinstance(request.get(field), str) or not request[field] for field in request
        ):
            raise ApplicationError(
                "representation evaluation request failed scope admission",
                type="representation_request_invalid",
                non_retryable=True,
            )
        result = await self._repository.evaluate_representation(request)
        try:
            validate_record(result, "representation_operation")
        except ContractViolation as exc:
            raise ApplicationError(
                "representation evaluation returned a malformed decision",
                type="representation_result_invalid",
                non_retryable=True,
            ) from exc
        if (
            result.get("tenantId") != request["tenant_id"]
            or result.get("journeyId") != request["journey_id"]
        ):
            raise ApplicationError(
                "representation evaluation crossed its workflow scope",
                type="representation_result_conflict",
                non_retryable=True,
            )
        return result


def buyer_journey_workflow_runner() -> SandboxedWorkflowRunner:
    """Keep Temporal sandboxing enabled while passing through the preloaded application package."""
    return SandboxedWorkflowRunner(
        restrictions=SandboxRestrictions.default.with_passthrough_modules("buyer_ops_contracts")
    )


def buyer_journey_workflow_id(tenant_id: str, journey_id: str) -> str:
    """Produce one bounded, tenant-scoped Temporal identity without delimiter ambiguity."""
    if not tenant_id or not journey_id:
        raise ValueError("tenant_id and journey_id are required")
    digest = hashlib.sha256(f"{len(tenant_id)}:{tenant_id}{journey_id}".encode()).hexdigest()
    return f"buyer-journey:{digest}"


async def start_buyer_journey_workflow(
    client: Client,
    workflow_input: dict[str, Any],
    *,
    task_queue: str,
) -> Any:
    """Validate and start the sole durable workflow for a tenant/BuyerJourney pair."""
    validate_record(workflow_input, "temporal")
    if workflow_input.get("message_type") != "workflow_input":
        raise ValueError("workflow_input must be an OT-01 workflow input message")
    if not task_queue:
        raise ValueError("task_queue is required")
    return await client.start_workflow(
        BuyerJourneyWorkflow.run,
        workflow_input,
        id=buyer_journey_workflow_id(workflow_input["tenant_id"], workflow_input["journey_id"]),
        task_queue=task_queue,
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
    )


def create_temporal_worker(
    client: Client,
    configuration: dict[str, Any],
    *,
    activities: Sequence[Callable[..., Any]],
) -> Worker:
    """Build the fixed PKT-04 worker inventory from explicit versioned configuration."""
    validate_record(configuration, "temporal")
    if configuration.get("message_type") != "worker_configuration":
        raise ValueError("configuration must be a Temporal worker configuration")
    if not activities:
        raise ValueError("at least one governed activity is required")
    return Worker(
        client,
        task_queue=configuration["task_queue"],
        workflows=[
            BuyerJourneyWorkflow,
            QualificationChildWorkflow,
            NurtureChildWorkflow,
            ConsultationChildWorkflow,
            RepresentationOnboardingWorkflow,
            TransactionCoordinationWorkflow,
            ConnectorReconciliationWorkflow,
        ],
        activities=activities,
        workflow_runner=buyer_journey_workflow_runner(),
        max_concurrent_workflow_tasks=configuration["max_concurrent_workflow_tasks"],
        max_concurrent_activities=configuration["max_concurrent_activities"],
        max_cached_workflows=configuration["max_cached_workflows"],
        graceful_shutdown_timeout=timedelta(seconds=configuration["graceful_shutdown_seconds"]),
    )


@workflow.defn(name="BuyerJourneyWorkflow", versioning_behavior=VersioningBehavior.PINNED)
class BuyerJourneyWorkflow:
    """Long-lived execution view; PostgreSQL reconciliation always supplies business state."""

    def __init__(self) -> None:
        self._input: dict[str, Any] | None = None
        self._state: dict[str, Any] | None = None
        self._requested_epoch = 1
        self._reconciled_epoch = 0
        self._seen_event_ids: set[str] = set()
        self._stopping = False
        self._paused = False

    @workflow.run
    async def run(self, workflow_input: dict[str, Any]) -> dict[str, Any]:
        self._validate_input(workflow_input)
        self._input = workflow_input
        while not self._stopping:
            if self._paused:
                await workflow.wait_condition(lambda: self._stopping or not self._paused)
                continue
            if self._requested_epoch > self._reconciled_epoch:
                requested_epoch = self._requested_epoch
                state = await self._reconcile()
                self._accept_state(state)
                self._reconciled_epoch = requested_epoch
                continue
            await workflow.wait_condition(
                lambda: self._stopping or self._requested_epoch > self._reconciled_epoch
            )
        if self._state is None:
            raise ApplicationError(
                "workflow stopped before canonical reconciliation",
                type="canonical_state_unavailable",
                non_retryable=True,
            )
        return self._state

    @workflow.signal
    def canonical_changed(self, signal: dict[str, Any]) -> None:
        current = self._require_input()
        required = {
            "message_type",
            "schema_version",
            "tenant_id",
            "journey_id",
            "event_id",
            "observed_canonical_version",
        }
        if set(signal) != required or (
            signal.get("message_type") != "canonical_changed"
            or signal.get("schema_version") != "ot01-canonical-change/1.0.0"
            or signal.get("tenant_id") != current["tenant_id"]
            or signal.get("journey_id") != current["journey_id"]
            or not isinstance(signal.get("event_id"), str)
            or not signal["event_id"]
            or not isinstance(signal.get("observed_canonical_version"), int)
            or signal["observed_canonical_version"] < 1
        ):
            # Signals have no response channel; upstream event admission emits the typed rejection.
            # Ignoring here is fail-closed and cannot alter or disclose this workflow's state.
            return
        event_id = signal["event_id"]
        if event_id in self._seen_event_ids:
            return
        self._seen_event_ids.add(event_id)
        if (
            self._state is not None
            and signal["observed_canonical_version"] <= self._state["canonical_version"]
        ):
            return
        self._requested_epoch += 1

    @workflow.signal
    def stop(self) -> None:
        """Graceful drain hook; production cancellation/termination remains operator-owned."""
        self._stopping = True

    @workflow.signal
    def pause(self) -> None:
        self._paused = True

    @workflow.signal
    def resume(self) -> None:
        self._paused = False
        self._requested_epoch += 1

    @workflow.query
    def current_state(self) -> dict[str, Any] | None:
        return self._state

    async def _reconcile(self) -> dict[str, Any]:
        current = self._require_input()
        policy = current["runtime_policy"]
        result = await workflow.execute_activity(
            "reconcile_journey_state",
            {
                "tenant_id": current["tenant_id"],
                "journey_id": current["journey_id"],
            },
            result_type=dict,
            start_to_close_timeout=timedelta(seconds=policy["start_to_close_seconds"]),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=policy["initial_retry_seconds"]),
                backoff_coefficient=policy["backoff_coefficient"],
                maximum_interval=timedelta(seconds=policy["maximum_retry_seconds"]),
                maximum_attempts=policy["maximum_attempts"],
            ),
        )
        if not isinstance(result, dict):
            raise ApplicationError(
                "canonical reconciliation returned a non-object state",
                type="canonical_state_invalid",
                non_retryable=True,
            )
        return result

    def _accept_state(self, state: dict[str, Any]) -> None:
        current = self._require_input()
        if (
            state.get("message_type") != "journey_state"
            or state.get("schema_version") != "ot01-journey-state/1.0.0"
            or state.get("journey_id") != current["journey_id"]
            or not isinstance(state.get("canonical_version"), int)
            or state["canonical_version"] < 1
            or (
                self._state is not None
                and state["canonical_version"] < self._state["canonical_version"]
            )
        ):
            raise ApplicationError(
                "canonical reconciliation returned mismatched or regressed state",
                type="canonical_state_conflict",
                non_retryable=True,
            )
        self._state = state

    def _require_input(self) -> dict[str, Any]:
        if self._input is None:
            raise ApplicationError(
                "workflow input is unavailable",
                type="workflow_not_initialized",
                non_retryable=True,
            )
        return self._input

    @staticmethod
    def _validate_input(value: dict[str, Any]) -> None:
        required = {
            "message_type",
            "schema_version",
            "tenant_id",
            "journey_id",
            "workflow_code_version",
            "runtime_policy",
        }
        policy_required = {
            "start_to_close_seconds",
            "initial_retry_seconds",
            "backoff_coefficient",
            "maximum_retry_seconds",
            "maximum_attempts",
        }
        policy = value.get("runtime_policy")
        if (
            set(value) != required
            or value.get("message_type") != "workflow_input"
            or value.get("schema_version") != "ot01-workflow/1.0.0"
            or value.get("workflow_code_version") != 1
            or not isinstance(value.get("tenant_id"), str)
            or not value["tenant_id"]
            or not isinstance(value.get("journey_id"), str)
            or not value["journey_id"]
            or not isinstance(policy, dict)
            or set(policy) != policy_required
            or any(
                not isinstance(policy[field], int) or policy[field] < 1
                for field in policy_required - {"backoff_coefficient"}
            )
            or not isinstance(policy["backoff_coefficient"], int | float)
            or policy["backoff_coefficient"] < 1
        ):
            raise ApplicationError(
                "workflow input failed closed schema admission",
                type="workflow_input_invalid",
                non_retryable=True,
            )


@workflow.defn(
    name="ConnectorReconciliationWorkflow", versioning_behavior=VersioningBehavior.PINNED
)
class ConnectorReconciliationWorkflow:
    """Durably reconcile an unknown provider outcome before any repeat is eligible."""

    @workflow.run
    async def run(self, workflow_input: dict[str, Any]) -> dict[str, Any]:
        self._validate_input(workflow_input)
        policy = workflow_input["runtime_policy"]
        while True:
            result = await workflow.execute_activity(
                "reconcile_unknown_effect",
                {
                    "tenant_id": workflow_input["tenant_id"],
                    "journey_id": workflow_input["journey_id"],
                    "effect_attempt_id": workflow_input["effect_attempt_id"],
                },
                result_type=dict,
                start_to_close_timeout=timedelta(seconds=policy["start_to_close_seconds"]),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=policy["initial_retry_seconds"]),
                    backoff_coefficient=policy["backoff_coefficient"],
                    maximum_interval=timedelta(seconds=policy["maximum_retry_seconds"]),
                    maximum_attempts=policy["maximum_attempts"],
                ),
            )
            self._validate_result(workflow_input, result)
            if result["attempt_state"] in {
                "reconciled_failed",
                "reconciled_succeeded",
            }:
                return cast(dict[str, Any], result)
            await workflow.sleep(
                timedelta(seconds=workflow_input["reconciliation_interval_seconds"])
            )

    @staticmethod
    def _validate_input(value: dict[str, Any]) -> None:
        required = {
            "message_type",
            "schema_version",
            "tenant_id",
            "journey_id",
            "parent_workflow_id",
            "effect_attempt_id",
            "workflow_code_version",
            "runtime_policy",
            "reconciliation_interval_seconds",
        }
        if (
            set(value) != required
            or value.get("message_type") != "connector_reconciliation_input"
            or value.get("schema_version") != "ot01-connector-reconciliation/1.0.0"
            or value.get("workflow_code_version") != 1
            or not isinstance(value.get("reconciliation_interval_seconds"), int)
            or value["reconciliation_interval_seconds"] < 1
            or any(
                not isinstance(value.get(field), str) or not value[field]
                for field in {
                    "tenant_id",
                    "journey_id",
                    "parent_workflow_id",
                    "effect_attempt_id",
                }
            )
            or not isinstance(value.get("runtime_policy"), dict)
        ):
            raise ApplicationError(
                "connector reconciliation input failed closed schema admission",
                type="workflow_input_invalid",
                non_retryable=True,
            )
        BuyerJourneyWorkflow._validate_input(
            {
                "message_type": "workflow_input",
                "schema_version": "ot01-workflow/1.0.0",
                "tenant_id": value["tenant_id"],
                "journey_id": value["journey_id"],
                "workflow_code_version": 1,
                "runtime_policy": value["runtime_policy"],
            }
        )

    @staticmethod
    def _validate_result(workflow_input: dict[str, Any], result: Any) -> None:
        allowed = {
            "message_type",
            "schema_version",
            "effect_attempt_id",
            "attempt_state",
            "provider_receipt_id",
        }
        if (
            not isinstance(result, dict)
            or not set(result).issubset(allowed)
            or not {
                "message_type",
                "schema_version",
                "effect_attempt_id",
                "attempt_state",
            }.issubset(result)
            or result.get("message_type") != "effect_reconciliation_state"
            or result.get("schema_version") != "ot01-effect-reconciliation-state/1.0.0"
            or result.get("effect_attempt_id") != workflow_input["effect_attempt_id"]
            or result.get("attempt_state")
            not in {"unknown_outcome", "reconciled_failed", "reconciled_succeeded"}
            or (
                result.get("attempt_state") == "reconciled_succeeded"
                and not result.get("provider_receipt_id")
            )
        ):
            raise ApplicationError(
                "connector reconciliation returned invalid or mismatched state",
                type="canonical_state_conflict",
                non_retryable=True,
            )


class _DomainChildCore:
    """Shared deterministic machinery; subclasses expose distinct Temporal workflow types."""

    def __init__(self, child_type: str, decision_activity: str | None = None) -> None:
        self.child_type = child_type
        self.decision_activity = decision_activity
        self.workflow_input: dict[str, Any] | None = None
        self.state: dict[str, Any] | None = None
        self.decision: dict[str, Any] | None = None
        self.requested_epoch = 1
        self.reconciled_epoch = 0
        self.seen_event_ids: set[str] = set()
        self.stopping = False

    async def run(self, workflow_input: dict[str, Any]) -> dict[str, Any]:
        self._validate_input(workflow_input)
        self.workflow_input = workflow_input
        while not self.stopping:
            if self.requested_epoch > self.reconciled_epoch:
                requested_epoch = self.requested_epoch
                state = await self._reconcile()
                self._accept_state(state)
                if self.decision_activity is not None:
                    self.decision = await workflow.execute_activity(
                        self.decision_activity,
                        {
                            "tenant_id": workflow_input["tenant_id"],
                            "journey_id": workflow_input["journey_id"],
                        },
                        result_type=dict,
                        start_to_close_timeout=timedelta(
                            seconds=workflow_input["runtime_policy"]["start_to_close_seconds"]
                        ),
                        retry_policy=RetryPolicy(
                            initial_interval=timedelta(
                                seconds=workflow_input["runtime_policy"]["initial_retry_seconds"]
                            ),
                            backoff_coefficient=workflow_input["runtime_policy"][
                                "backoff_coefficient"
                            ],
                            maximum_interval=timedelta(
                                seconds=workflow_input["runtime_policy"]["maximum_retry_seconds"]
                            ),
                            maximum_attempts=workflow_input["runtime_policy"]["maximum_attempts"],
                        ),
                    )
                self.reconciled_epoch = requested_epoch
                continue
            await workflow.wait_condition(
                lambda: self.stopping or self.requested_epoch > self.reconciled_epoch
            )
        if self.state is None:
            raise ApplicationError(
                "child stopped before canonical reconciliation",
                type="canonical_state_unavailable",
                non_retryable=True,
            )
        return self.state

    def canonical_changed(self, signal: dict[str, Any]) -> None:
        current = self._require_input()
        if (
            set(signal)
            != {
                "message_type",
                "schema_version",
                "tenant_id",
                "journey_id",
                "event_id",
                "observed_canonical_version",
            }
            or signal.get("message_type") != "canonical_changed"
            or signal.get("schema_version") != "ot01-canonical-change/1.0.0"
            or signal.get("tenant_id") != current["tenant_id"]
            or signal.get("journey_id") != current["journey_id"]
            or not isinstance(signal.get("event_id"), str)
            or not signal["event_id"]
            or not isinstance(signal.get("observed_canonical_version"), int)
            or signal["observed_canonical_version"] < 1
        ):
            return
        if signal["event_id"] in self.seen_event_ids:
            return
        self.seen_event_ids.add(signal["event_id"])
        if (
            self.state is not None
            and signal["observed_canonical_version"] <= self.state["canonical_version"]
        ):
            return
        self.requested_epoch += 1

    async def _reconcile(self) -> dict[str, Any]:
        current = self._require_input()
        policy = current["runtime_policy"]
        result = await workflow.execute_activity(
            "reconcile_journey_state",
            {"tenant_id": current["tenant_id"], "journey_id": current["journey_id"]},
            result_type=dict,
            start_to_close_timeout=timedelta(seconds=policy["start_to_close_seconds"]),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=policy["initial_retry_seconds"]),
                backoff_coefficient=policy["backoff_coefficient"],
                maximum_interval=timedelta(seconds=policy["maximum_retry_seconds"]),
                maximum_attempts=policy["maximum_attempts"],
            ),
        )
        if not isinstance(result, dict):
            raise ApplicationError(
                "child canonical reconciliation returned a non-object",
                type="canonical_state_invalid",
                non_retryable=True,
            )
        return result

    def _accept_state(self, state: dict[str, Any]) -> None:
        current = self._require_input()
        if (
            state.get("message_type") != "journey_state"
            or state.get("schema_version") != "ot01-journey-state/1.0.0"
            or state.get("journey_id") != current["journey_id"]
            or not isinstance(state.get("canonical_version"), int)
            or state["canonical_version"] < 1
            or (
                self.state is not None
                and state["canonical_version"] < self.state["canonical_version"]
            )
        ):
            raise ApplicationError(
                "child canonical state mismatched or regressed",
                type="canonical_state_conflict",
                non_retryable=True,
            )
        self.state = state

    def _require_input(self) -> dict[str, Any]:
        if self.workflow_input is None:
            raise ApplicationError(
                "child workflow input is unavailable",
                type="workflow_not_initialized",
                non_retryable=True,
            )
        return self.workflow_input

    def _validate_input(self, value: dict[str, Any]) -> None:
        if (
            set(value)
            != {
                "message_type",
                "schema_version",
                "child_type",
                "tenant_id",
                "journey_id",
                "parent_workflow_id",
                "workflow_code_version",
                "runtime_policy",
            }
            or value.get("message_type") != "domain_child_input"
            or value.get("schema_version") != "ot01-domain-child/1.0.0"
            or value.get("child_type") != self.child_type
            or not isinstance(value.get("parent_workflow_id"), str)
            or not value["parent_workflow_id"]
        ):
            raise ApplicationError(
                "domain child input failed closed schema admission",
                type="workflow_input_invalid",
                non_retryable=True,
            )
        BuyerJourneyWorkflow._validate_input(
            {
                "message_type": "workflow_input",
                "schema_version": "ot01-workflow/1.0.0",
                "tenant_id": value["tenant_id"],
                "journey_id": value["journey_id"],
                "workflow_code_version": value["workflow_code_version"],
                "runtime_policy": value["runtime_policy"],
            }
        )


@workflow.defn(name="QualificationChildWorkflow", versioning_behavior=VersioningBehavior.PINNED)
class QualificationChildWorkflow:
    def __init__(self) -> None:
        self._core = _DomainChildCore("qualification", "evaluate_qualification")

    @workflow.run
    async def run(self, workflow_input: dict[str, Any]) -> dict[str, Any]:
        return await self._core.run(workflow_input)

    @workflow.signal
    def canonical_changed(self, signal: dict[str, Any]) -> None:
        self._core.canonical_changed(signal)

    @workflow.signal
    def stop(self) -> None:
        self._core.stopping = True

    @workflow.query
    def current_state(self) -> dict[str, Any] | None:
        return self._core.state

    @workflow.query
    def current_decision(self) -> dict[str, Any] | None:
        return self._core.decision


@workflow.defn(name="NurtureChildWorkflow", versioning_behavior=VersioningBehavior.PINNED)
class NurtureChildWorkflow:
    def __init__(self) -> None:
        self._core = _DomainChildCore("nurture", "evaluate_nurture")

    @workflow.run
    async def run(self, workflow_input: dict[str, Any]) -> dict[str, Any]:
        return await self._core.run(workflow_input)

    @workflow.signal
    def canonical_changed(self, signal: dict[str, Any]) -> None:
        self._core.canonical_changed(signal)

    @workflow.signal
    def stop(self) -> None:
        self._core.stopping = True

    @workflow.query
    def current_state(self) -> dict[str, Any] | None:
        return self._core.state

    @workflow.query
    def current_decision(self) -> dict[str, Any] | None:
        return self._core.decision


@workflow.defn(name="ConsultationChildWorkflow", versioning_behavior=VersioningBehavior.PINNED)
class ConsultationChildWorkflow:
    def __init__(self) -> None:
        self._core = _DomainChildCore("consultation", "evaluate_consultation")

    @workflow.run
    async def run(self, workflow_input: dict[str, Any]) -> dict[str, Any]:
        return await self._core.run(workflow_input)

    @workflow.signal
    def canonical_changed(self, signal: dict[str, Any]) -> None:
        self._core.canonical_changed(signal)

    @workflow.signal
    def stop(self) -> None:
        self._core.stopping = True

    @workflow.query
    def current_state(self) -> dict[str, Any] | None:
        return self._core.state

    @workflow.query
    def current_decision(self) -> dict[str, Any] | None:
        return self._core.decision


@workflow.defn(
    name="RepresentationOnboardingWorkflow", versioning_behavior=VersioningBehavior.PINNED
)
class RepresentationOnboardingWorkflow:
    """Durable representation state; provider execution remains separately governed."""

    def __init__(self) -> None:
        self._decision: dict[str, Any] | None = None
        self._stopping = False

    @workflow.run
    async def run(self, workflow_input: dict[str, Any]) -> dict[str, Any]:
        if (
            set(workflow_input)
            != {"message_type", "schema_version", "tenant_id", "journey_id", "runtime_policy"}
            or workflow_input.get("message_type") != "representation_workflow_input"
            or workflow_input.get("schema_version") != "representation-operation/1.0.0"
            or any(
                not isinstance(workflow_input.get(field), str) or not workflow_input[field]
                for field in ("tenant_id", "journey_id")
            )
            or not isinstance(workflow_input.get("runtime_policy"), dict)
        ):
            raise ApplicationError(
                "representation workflow input failed schema admission",
                type="workflow_input_invalid",
                non_retryable=True,
            )
        policy = workflow_input["runtime_policy"]
        self._decision = await workflow.execute_activity(
            "evaluate_representation",
            {"tenant_id": workflow_input["tenant_id"], "journey_id": workflow_input["journey_id"]},
            result_type=dict,
            start_to_close_timeout=timedelta(seconds=policy["start_to_close_seconds"]),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=policy["initial_retry_seconds"]),
                backoff_coefficient=policy["backoff_coefficient"],
                maximum_interval=timedelta(seconds=policy["maximum_retry_seconds"]),
                maximum_attempts=policy["maximum_attempts"],
            ),
        )
        await workflow.wait_condition(lambda: self._stopping)
        return self._decision

    @workflow.signal
    def stop(self) -> None:
        self._stopping = True

    @workflow.query
    def current_decision(self) -> dict[str, Any] | None:
        return self._decision


@workflow.defn(
    name="TransactionCoordinationWorkflow", versioning_behavior=VersioningBehavior.PINNED
)
class TransactionCoordinationWorkflow:
    """Long-lived transaction plan that never treats model output as contract truth."""

    def __init__(self) -> None:
        self._plan: dict[str, Any] | None = None
        self._stopping = False

    @workflow.run
    async def run(self, workflow_input: dict[str, Any]) -> dict[str, Any]:
        self._validate_input(workflow_input)
        self._plan = await workflow.execute_activity(
            "evaluate_transaction",
            {
                "tenant_id": workflow_input["tenant_id"],
                "journey_id": workflow_input["journey_id"],
                "transaction_id": workflow_input["transaction_id"],
            },
            result_type=dict,
            start_to_close_timeout=timedelta(
                seconds=workflow_input["runtime_policy"]["start_to_close_seconds"]
            ),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(
                    seconds=workflow_input["runtime_policy"]["initial_retry_seconds"]
                ),
                backoff_coefficient=workflow_input["runtime_policy"]["backoff_coefficient"],
                maximum_interval=timedelta(
                    seconds=workflow_input["runtime_policy"]["maximum_retry_seconds"]
                ),
                maximum_attempts=workflow_input["runtime_policy"]["maximum_attempts"],
            ),
        )
        await workflow.wait_condition(lambda: self._stopping)
        return self._plan

    @workflow.signal
    def stop(self) -> None:
        self._stopping = True

    @workflow.query
    def current_plan(self) -> dict[str, Any] | None:
        return self._plan

    @staticmethod
    def _validate_input(value: dict[str, Any]) -> None:
        if (
            set(value)
            != {
                "message_type",
                "schema_version",
                "tenant_id",
                "journey_id",
                "transaction_id",
                "runtime_policy",
            }
            or value.get("message_type") != "transaction_workflow_input"
            or value.get("schema_version") != "transaction-coordination/1.0.0"
            or any(
                not isinstance(value.get(field), str) or not value[field]
                for field in ("tenant_id", "journey_id", "transaction_id")
            )
            or not isinstance(value.get("runtime_policy"), dict)
        ):
            raise ApplicationError(
                "transaction workflow input failed schema admission",
                type="workflow_input_invalid",
                non_retryable=True,
            )
