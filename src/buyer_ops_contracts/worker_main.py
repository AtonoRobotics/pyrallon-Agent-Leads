"""Temporal worker process with owner-supplied runtime and journey-state semantics."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any, cast

import psycopg
from temporalio.client import Client

from .canonical_repository import CanonicalRepository, Connection
from .connector_authorization import (
    PlatformOAuthStore,
    load_connector_credential,
    refresh_connector_credential,
)
from .connector_gateway import ConnectorAdapter
from .connector_runtime import configured_adapters_from_environment
from .consultation_runtime import ConsultationRuntime
from .journey_state import compile_journey_state
from .nurture_runtime import NurtureRuntime
from .operator_commands import OperatorCommandService, TemporalWorkflowSignalDispatcher
from .provider_adapters import DirectProviderAdapter, DirectProviderConfig
from .qualification_runtime import QualificationRuntime
from .representation_operation_runtime import RepresentationOperationRuntime
from .structural import validate_record
from .temporal_workflows import (
    ConsultationActivities,
    NurtureActivities,
    QualificationActivities,
    ReconciliationActivities,
    RepresentationActivities,
    TransactionActivities,
    create_temporal_worker,
)
from .transaction_runtime import TransactionRuntime

_LOGGER = logging.getLogger(__name__)


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
                compilation = compile_journey_state(
                    tenant_id=tenant_id,
                    journey_id=journey_id,
                    canonical_version=int(journey["version"]),
                    records=repository.current_records(),
                    observed_at=datetime.now(UTC),
                )
                return compilation.state
            finally:
                connection.close()

        return await asyncio.to_thread(load)


class PostgresEffectReconciliationRepository:
    """Resolve provider truth and append the terminal EffectAttempt state."""

    def __init__(
        self, dsn: str, adapters: dict[str, ConnectorAdapter], permit_secret: bytes | None = None
    ) -> None:
        if not dsn:
            raise ValueError("database DSN is required")
        self._dsn = dsn
        self._adapters = adapters
        self._permit_secret = permit_secret

    async def reconcile_unknown_effect(self, request: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._reconcile, request)

    def _reconcile(self, request: dict[str, Any]) -> dict[str, Any]:
        tenant_id = request["tenant_id"]
        attempt_id = request["effect_attempt_id"]
        connection = psycopg.connect(self._dsn)
        try:
            repository = CanonicalRepository(cast(Connection, connection), tenant_id=tenant_id)
            attempt = repository.get(f"effect-attempt:{attempt_id}")
            if attempt is None or attempt.get("recordType") != "EffectAttempt":
                return _unknown_reconciliation(attempt_id)
            if attempt.get("attemptState") != "unknown_outcome":
                return _reconciliation_state(attempt, effect_attempt_id=attempt_id)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT intent
                    FROM habitat_authority_decisions
                    WHERE tenant_id = %s AND intent_id = %s
                    ORDER BY ordering_token DESC
                    LIMIT 1
                    """,
                    (tenant_id, attempt["intentId"]),
                )
                row = cursor.fetchone()
            connection.commit()
            if row is None or not isinstance(row[0], dict):
                return _unknown_reconciliation(attempt_id)
            intent = cast(dict[str, Any], row[0])
            if intent.get("buyer_journey_id") != request["journey_id"]:
                raise ValueError("effect intent is bound to a different journey")
            context = intent.get("effect_context")
            if not isinstance(context, dict):
                return _unknown_reconciliation(attempt_id)
            connector_id = str(intent.get("connector_binding_id") or "")
            adapter = self._adapters.get(connector_id)
            if adapter is None and self._permit_secret is not None:
                credential = load_connector_credential(
                    connection,
                    tenant_id=tenant_id,
                    grant_id=str(context.get("grant_id") or ""),
                    connector_id=connector_id,
                    permit_secret=self._permit_secret,
                    now=datetime.now(UTC),
                )
                if credential is None:
                    credential = refresh_connector_credential(
                        connection,
                        tenant_id=tenant_id,
                        grant_id=str(context.get("grant_id") or ""),
                        connector_id=connector_id,
                        permit_secret=self._permit_secret,
                        now=datetime.now(UTC),
                        oauth_clients=PlatformOAuthStore(
                            connection, permit_secret=self._permit_secret
                        ).material(),
                    )
                if credential is not None:
                    stored_connector, provider, account_id, token = credential
                    if stored_connector == connector_id:
                        adapter = DirectProviderAdapter(
                            DirectProviderConfig.from_value(
                                {
                                    "connectorId": connector_id,
                                    "provider": provider,
                                    "credentialEnv": "BUYER_OPS_DATABASE_CREDENTIAL",
                                    "accountId": account_id,
                                }
                            ),
                            credential=token,
                        )
            receipt = attempt.get("providerReceiptId")
            reconcile = getattr(adapter, "reconcile", None) if adapter is not None else None
            if not callable(reconcile):
                return _unknown_reconciliation(attempt_id)
            result = reconcile(
                {
                    "schemaVersion": "connector-gateway/1.0.0",
                    "tenantId": tenant_id,
                    "connectorId": connector_id,
                    "grantId": context.get("grant_id"),
                    "grantVersion": context.get("grant_version"),
                    "capability": context.get("capability_id"),
                    "delegatedPrincipalId": context.get("delegated_principal_id"),
                    "correlationId": intent.get("trace_id"),
                    "occurredAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "requestId": intent.get("intent_id"),
                    "idempotencyKey": intent.get("idempotency_key"),
                    "payloadDigest": attempt.get("payloadDigest"),
                },
                str(receipt) if receipt else None,
            )
            state = str(result.get("attemptState"))
            if state not in {"unknown_outcome", "reconciled_failed", "reconciled_succeeded"}:
                raise ValueError("provider reconciliation returned an invalid attempt state")
            updated = dict(attempt)
            updated["version"] = int(attempt["version"]) + 1
            updated["updatedAt"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            updated["attemptState"] = state
            if result.get("providerReceiptId"):
                updated["providerReceiptId"] = result["providerReceiptId"]
            saved = repository.save(updated, expected_version=int(attempt["version"]))
            return _reconciliation_state(saved, effect_attempt_id=attempt_id)
        finally:
            connection.close()


class PostgresQualificationEvaluationRepository:
    """Load the canonical qualification snapshot and evaluate deployment policy."""

    def __init__(self, dsn: str, policy: dict[str, Any], runtime_config: dict[str, Any]) -> None:
        self._dsn = dsn
        self._policy = policy
        self._runtime_config = runtime_config
        self._runtime = QualificationRuntime(
            deriver_principal_id=str(runtime_config["deriverPrincipalId"]),
            implementation_version=str(runtime_config["implementationVersion"]),
        )

    async def evaluate_qualification(self, request: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._evaluate, request)

    def _evaluate(self, request: dict[str, Any]) -> dict[str, Any]:
        connection = psycopg.connect(self._dsn)
        try:
            repository = CanonicalRepository(
                cast(Connection, connection), tenant_id=request["tenant_id"]
            )
            journey = repository.get(request["journey_id"])
            if journey is None or journey.get("recordType") != "BuyerJourney":
                raise KeyError(request["journey_id"])
            observations = repository.list_by_type("QualificationObservation")
            return self._runtime.evaluate(
                policy=self._policy,
                journey=journey,
                observations=observations,
                evaluated_at=datetime.now(UTC),
                service_zone_decision_ref=self._runtime_config["serviceZoneDecisionRef"],
                service_zone_eligible=bool(self._runtime_config["serviceZoneEligible"]),
                capacity_decision_ref=self._runtime_config["capacityDecisionRef"],
                capacity_available=bool(self._runtime_config["capacityAvailable"]),
                urgent_escalation_refs=list(self._runtime_config.get("urgentEscalationRefs", [])),
            )
        finally:
            connection.close()


class PostgresNurtureEvaluationRepository:
    """Load canonical journey context and produce a consent-bounded nurture plan."""

    def __init__(self, dsn: str, policy: dict[str, Any], runtime_config: dict[str, Any]) -> None:
        self._dsn = dsn
        self._policy = policy
        self._runtime_config = runtime_config
        self._runtime = NurtureRuntime(
            deriver_id=str(runtime_config["deriverId"]),
            implementation_version=str(runtime_config["implementationVersion"]),
        )

    async def evaluate_nurture(self, request: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._evaluate, request)

    def _evaluate(self, request: dict[str, Any]) -> dict[str, Any]:
        connection = psycopg.connect(self._dsn)
        try:
            repository = CanonicalRepository(
                cast(Connection, connection), tenant_id=request["tenant_id"]
            )
            journey = repository.get(request["journey_id"])
            if journey is None or journey.get("recordType") != "BuyerJourney":
                raise KeyError(request["journey_id"])
            records = repository.current_records()
            compilation = compile_journey_state(
                tenant_id=request["tenant_id"],
                journey_id=request["journey_id"],
                canonical_version=int(journey["version"]),
                records=records,
                observed_at=datetime.now(UTC),
            )
            consent_states = [
                str(record.get("validityState"))
                for record in records
                if record.get("recordType") == "ConsentGrant"
            ]
            if any(state == "revoked" for state in consent_states):
                consent_state = "revoked"
            elif any(state == "active" for state in consent_states):
                consent_state = "granted"
            else:
                consent_state = "unknown"
            contactability = str(compilation.state["contactability_state"])
            journey_messages = [
                record for record in records if record.get("recordType") == "Message"
            ]
            last_interaction = max(
                (str(record["sentOrReceivedAt"]) for record in journey_messages),
                default=None,
            )
            commitments = [
                record
                for record in records
                if record.get("recordType") == "Commitment"
                and record.get("journeyId") == request["journey_id"]
            ]
            return self._runtime.plan(
                policy=self._policy,
                journey=journey,
                consent_state=consent_state,
                contactability_state=contactability,
                representation_state=str(journey["representationState"]),
                now=datetime.now(UTC),
                last_interaction_at=last_interaction,
                unresolved_commitments=commitments,
            )
        finally:
            connection.close()


class PostgresConsultationEvaluationRepository:
    """Load canonical journey, appointment, and evidence state for consultation decisions."""

    def __init__(self, dsn: str, reminder_policy: dict[str, Any] | None = None) -> None:
        self._dsn = dsn
        self._runtime = ConsultationRuntime()
        self._reminder_policy = reminder_policy

    async def evaluate_consultation(self, request: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._evaluate, request)

    def _evaluate(self, request: dict[str, Any]) -> dict[str, Any]:
        connection = psycopg.connect(self._dsn)
        try:
            repository = CanonicalRepository(
                cast(Connection, connection), tenant_id=request["tenant_id"]
            )
            journey = repository.get(request["journey_id"])
            if journey is None or journey.get("recordType") != "BuyerJourney":
                raise KeyError(request["journey_id"])
            records = repository.current_records()
            state = compile_journey_state(
                tenant_id=request["tenant_id"],
                journey_id=request["journey_id"],
                canonical_version=int(journey["version"]),
                records=records,
                observed_at=datetime.now(UTC),
            )
            return self._runtime.evaluate(
                journey=journey,
                journey_state=state.state,
                appointments=[
                    record for record in records if record.get("recordType") == "Appointment"
                ],
                reminder_policy=self._reminder_policy,
                recipient_endpoints=[
                    record for record in records if record.get("recordType") == "ContactEndpoint"
                ],
                consent_state=str(state.state.get("consent_state", "unknown")),
                contactability_state=str(state.state.get("contactability_state", "unknown")),
                evidence_records=[
                    record
                    for record in records
                    if record.get("recordType")
                    in {
                        "Evidence",
                        "Assertion",
                        "VerifiedFact",
                        "Inference",
                        "Memory",
                        "DocumentArtifact",
                    }
                ],
                now=datetime.now(UTC),
            )
        finally:
            connection.close()


class PostgresRepresentationEvaluationRepository:
    """Load current IABS and agreement records for durable onboarding state."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._runtime = RepresentationOperationRuntime()

    async def evaluate_representation(self, request: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._evaluate, request)

    def _evaluate(self, request: dict[str, Any]) -> dict[str, Any]:
        connection = psycopg.connect(self._dsn)
        try:
            repository = CanonicalRepository(
                cast(Connection, connection), tenant_id=request["tenant_id"]
            )
            journey = repository.get(request["journey_id"])
            if journey is None or journey.get("recordType") != "BuyerJourney":
                raise KeyError(request["journey_id"])
            records = repository.current_records()
            return self._runtime.evaluate(
                journey=journey,
                agreements=[
                    record
                    for record in records
                    if record.get("recordType") == "WrittenBuyerAgreement"
                ],
                iabs_deliveries=[
                    record for record in records if record.get("recordType") == "IabsDelivery"
                ],
                now=datetime.now(UTC),
            )
        finally:
            connection.close()


class PostgresTransactionEvaluationRepository:
    """Load only canonical transaction records for a confirmed operational plan."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._runtime = TransactionRuntime()

    async def evaluate_transaction(self, request: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._evaluate, request)

    def _evaluate(self, request: dict[str, Any]) -> dict[str, Any]:
        connection = psycopg.connect(self._dsn)
        try:
            repository = CanonicalRepository(
                cast(Connection, connection), tenant_id=request["tenant_id"]
            )
            transaction = repository.get(request["transaction_id"])
            if transaction is None or transaction.get("recordType") != "Transaction":
                raise KeyError(request["transaction_id"])
            if transaction.get("journeyId") != request["journey_id"]:
                raise ValueError("transaction is bound to a different journey")
            records = repository.current_records()
            return self._runtime.build_plan(
                transaction=transaction,
                milestones=[
                    record
                    for record in records
                    if record.get("recordType") == "TransactionMilestone"
                    and record.get("transactionId") == transaction["id"]
                ],
                confirmed_dates=[
                    record
                    for record in records
                    if record.get("recordType") == "ConfirmedTransactionDate"
                    and record.get("transactionId") == transaction["id"]
                ],
            )
        finally:
            connection.close()


def _unknown_reconciliation(effect_attempt_id: str) -> dict[str, Any]:
    return {
        "message_type": "effect_reconciliation_state",
        "schema_version": "ot01-effect-reconciliation-state/1.0.0",
        "effect_attempt_id": effect_attempt_id,
        "attempt_state": "unknown_outcome",
    }


def _reconciliation_state(
    attempt: dict[str, Any], *, effect_attempt_id: str | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "message_type": "effect_reconciliation_state",
        "schema_version": "ot01-effect-reconciliation-state/1.0.0",
        "effect_attempt_id": effect_attempt_id
        or str(attempt["id"]).removeprefix("effect-attempt:"),
        "attempt_state": str(attempt["attemptState"]),
    }
    if attempt.get("providerReceiptId"):
        result["provider_receipt_id"] = attempt["providerReceiptId"]
    return result


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


def load_outbox_tenants(raw: str | None = None) -> tuple[str, ...]:
    """Load the explicit tenant allow-list used by the outbox poller."""
    encoded = raw if raw is not None else os.environ.get("BUYER_OPS_OUTBOX_TENANTS", "")
    tenants = tuple(item.strip() for item in encoded.split(",") if item.strip())
    if not tenants:
        raise ValueError("BUYER_OPS_OUTBOX_TENANTS is required")
    if len(set(tenants)) != len(tenants):
        raise ValueError("BUYER_OPS_OUTBOX_TENANTS must not contain duplicates")
    return tenants


def load_qualification_configuration() -> tuple[dict[str, Any], dict[str, Any]]:
    policy_raw = os.environ.get("BUYER_OPS_QUALIFICATION_POLICY_JSON", "").strip()
    runtime_raw = os.environ.get("BUYER_OPS_QUALIFICATION_RUNTIME_JSON", "").strip()
    if not policy_raw or not runtime_raw:
        raise ValueError(
            "BUYER_OPS_QUALIFICATION_POLICY_JSON and "
            "BUYER_OPS_QUALIFICATION_RUNTIME_JSON are required"
        )
    policy = json.loads(policy_raw)
    runtime = json.loads(runtime_raw)
    if not isinstance(policy, dict) or not isinstance(runtime, dict):
        raise ValueError("qualification policy and runtime configuration must be objects")
    validate_record(policy, "qualification_readiness")
    if policy.get("messageType") != "qualification_policy":
        raise ValueError("qualification policy has the wrong message type")
    required_runtime = {
        "deriverPrincipalId",
        "implementationVersion",
        "serviceZoneDecisionRef",
        "serviceZoneEligible",
        "capacityDecisionRef",
        "capacityAvailable",
    }
    if set(runtime) - (
        required_runtime | {"urgentEscalationRefs"}
    ) or not required_runtime.issubset(runtime):
        raise ValueError("qualification runtime configuration is incomplete")
    return policy, runtime


def load_nurture_configuration() -> tuple[dict[str, Any], dict[str, Any]]:
    policy_raw = os.environ.get("BUYER_OPS_NURTURE_POLICY_JSON", "").strip()
    runtime_raw = os.environ.get("BUYER_OPS_NURTURE_RUNTIME_JSON", "").strip()
    if not policy_raw or not runtime_raw:
        raise ValueError(
            "BUYER_OPS_NURTURE_POLICY_JSON and BUYER_OPS_NURTURE_RUNTIME_JSON are required"
        )
    policy = json.loads(policy_raw)
    runtime = json.loads(runtime_raw)
    if not isinstance(policy, dict) or not isinstance(runtime, dict):
        raise ValueError("nurture policy and runtime configuration must be objects")
    validate_record(policy, "nurture_plan")
    if policy.get("messageType") != "nurture_policy":
        raise ValueError("nurture policy has the wrong message type")
    if set(runtime) != {"deriverId", "implementationVersion"}:
        raise ValueError("nurture runtime configuration is incomplete")
    return policy, runtime


def load_reminder_configuration(raw: str | None = None) -> dict[str, Any] | None:
    """Load the optional owner-published reminder policy; absence fails closed per journey."""
    encoded = raw if raw is not None else os.environ.get("BUYER_OPS_REMINDER_POLICY_JSON", "")
    if not encoded.strip():
        return None
    policy = json.loads(encoded)
    if not isinstance(policy, dict) or policy.get("messageType") != "reminder_policy":
        raise ValueError("BUYER_OPS_REMINDER_POLICY_JSON must be a reminder_policy object")
    return policy


async def run_outbox_dispatch_loop(
    dsn: str,
    client: Client,
    tenant_ids: tuple[str, ...],
    *,
    poll_interval_seconds: float = 2.0,
) -> None:
    """Continuously deliver committed workflow signals with bounded polling."""
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")
    dispatcher = TemporalWorkflowSignalDispatcher(client)
    while True:
        for tenant_id in tenant_ids:
            try:
                connection = psycopg.connect(dsn)
                try:
                    repository = CanonicalRepository(
                        cast(Connection, connection), tenant_id=tenant_id
                    )
                    service = OperatorCommandService(connection, repository, tenant_id=tenant_id)
                    await service.dispatch_workflow_outbox(dispatcher, limit=100)
                finally:
                    connection.close()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("outbox delivery pass failed for tenant %s", tenant_id)
        await asyncio.sleep(poll_interval_seconds)


async def _run() -> None:
    dsn = os.environ.get("BUYER_OPS_DATABASE_DSN") or os.environ.get("DATABASE_URL")
    address = os.environ.get("TEMPORAL_ADDRESS")
    namespace = os.environ.get("TEMPORAL_NAMESPACE")
    if not dsn or not address or not namespace:
        raise SystemExit(
            "BUYER_OPS_DATABASE_DSN, TEMPORAL_ADDRESS, and TEMPORAL_NAMESPACE are required"
        )
    configuration = load_worker_configuration()
    tenant_ids = load_outbox_tenants()
    connector_adapters = configured_adapters_from_environment()
    qualification_policy, qualification_runtime_config = load_qualification_configuration()
    nurture_policy, nurture_runtime_config = load_nurture_configuration()
    reminder_policy = load_reminder_configuration()
    client = await Client.connect(address, namespace=namespace)
    activities = ReconciliationActivities(
        PostgresJourneyStateRepository(dsn),
        PostgresEffectReconciliationRepository(
            dsn, connector_adapters, os.environ.get("BUYER_OPS_PERMIT_SECRET", "").encode()
        ),
    )
    qualification_activities = QualificationActivities(
        PostgresQualificationEvaluationRepository(
            dsn, qualification_policy, qualification_runtime_config
        )
    )
    nurture_activities = NurtureActivities(
        PostgresNurtureEvaluationRepository(dsn, nurture_policy, nurture_runtime_config)
    )
    consultation_activities = ConsultationActivities(
        PostgresConsultationEvaluationRepository(dsn, reminder_policy)
    )
    representation_activities = RepresentationActivities(
        PostgresRepresentationEvaluationRepository(dsn)
    )
    transaction_activities = TransactionActivities(PostgresTransactionEvaluationRepository(dsn))
    worker = create_temporal_worker(
        client,
        configuration,
        activities=[
            activities.reconcile_journey_state,
            activities.reconcile_unknown_effect,
            qualification_activities.evaluate_qualification,
            nurture_activities.evaluate_nurture,
            consultation_activities.evaluate_consultation,
            representation_activities.evaluate_representation,
            transaction_activities.evaluate_transaction,
        ],
    )
    outbox_task = asyncio.create_task(run_outbox_dispatch_loop(dsn, client, tenant_ids))
    try:
        await worker.run()
    finally:
        outbox_task.cancel()
        await asyncio.gather(outbox_task, return_exceptions=True)


def main() -> int:
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
