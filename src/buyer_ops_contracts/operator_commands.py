"""Dispatch operator-surface/1.1.0 OperatorCommand against canonical + Temporal."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, cast

from psycopg.types.json import Jsonb
from temporalio.client import Client

from .actor_authorization import (
    ActorTenantAuthorizationRepository,
    authorize_operator_command,
)
from .authority_activation_fair_housing import validate_authority_activation_fair_housing_semantics
from .canonical_repository import CanonicalRepository, VersionConflict
from .errors import ContractViolation
from .operator_contract import (
    OPERATOR_COMMAND_TARGETS,
    operator_payload_digest,
    validate_operator_semantics,
)
from .operator_policy import OperatorPolicyRepository
from .structural import validate_record

_CANONICAL_MUTATION_COMMANDS = frozenset(
    {
        "approve",
        "deny",
        "correct_replace",
        "correct_invalidate",
        "revoke_authorization",
        "revoke_approval",
    }
)
_WORKFLOW_COMMANDS = frozenset({"pause_workflow", "resume_workflow", "request_reconciliation"})


class WorkflowSignalDispatcher(Protocol):
    async def dispatch(
        self,
        *,
        workflow_id: str,
        run_id: str,
        signal_name: str,
        signal_id: str,
        payload: dict[str, Any],
    ) -> str: ...


class TemporalWorkflowSignalDispatcher:
    """Deliver the allow-listed operator signals through the Temporal client."""

    def __init__(self, client: Client) -> None:
        self._client = client

    async def dispatch(
        self,
        *,
        workflow_id: str,
        run_id: str,
        signal_name: str,
        signal_id: str,
        payload: dict[str, Any],
    ) -> str:
        if signal_name not in {"pause", "resume", "canonical_changed"}:
            raise ValueError(f"unsupported workflow signal: {signal_name}")
        if signal_name == "canonical_changed":
            if not payload:
                raise ValueError("canonical_changed requires a payload")
        elif payload:
            raise ValueError(f"{signal_name} does not accept a payload")
        handle = self._client.get_workflow_handle(workflow_id, run_id=run_id)
        if signal_name == "canonical_changed":
            await handle.signal(signal_name, payload)
        else:
            await handle.signal(signal_name)
        return f"temporal:{workflow_id}:{run_id}:{signal_id}"


class OperatorCommandError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool, detail: str) -> None:
        self.code = code
        self.retryable = retryable
        self.detail = detail
        super().__init__(detail)


class OperatorCommandService:
    def __init__(
        self,
        connection: Any,
        repository: CanonicalRepository,
        *,
        tenant_id: str,
        policy_repository: OperatorPolicyRepository | None = None,
    ) -> None:
        self._connection = connection
        self._repository = repository
        self._tenant_id = tenant_id
        self._policy_repository = policy_repository or OperatorPolicyRepository(
            connection, tenant_id=tenant_id
        )

    def dispatch(self, command: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        try:
            validate_record(command, "operator_surface")
            validate_operator_semantics(command)
        except ContractViolation as exc:
            raise OperatorCommandError(
                "validation_failed", retryable=False, detail=str(exc)
            ) from exc
        if command.get("message_type") != "operator_command":
            raise OperatorCommandError("validation_failed", retryable=False, detail="not a command")
        if command["tenant_id"] != self._tenant_id:
            raise OperatorCommandError(
                "authority_denied", retryable=False, detail="tenant mismatch"
            )
        now = datetime.now(UTC)
        try:
            issued = datetime.fromisoformat(command["issued_at"].replace("Z", "+00:00"))
            expires = datetime.fromisoformat(command["expires_at"].replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise OperatorCommandError(
                "validation_failed", retryable=False, detail="invalid command timestamp"
            ) from exc
        if expires <= now:
            raise OperatorCommandError(
                "approval_expired", retryable=False, detail="command expired"
            )
        if issued > now:
            raise OperatorCommandError(
                "validation_failed", retryable=False, detail="command not yet valid"
            )
        authority = command["authority"]
        if authority["actor_id"] != actor_id:
            raise OperatorCommandError("authority_denied", retryable=False, detail="actor mismatch")
        grant = ActorTenantAuthorizationRepository(
            self._connection, tenant_id=self._tenant_id
        ).current(actor_id, now=now)
        if grant is None:
            raise OperatorCommandError(
                "authority_denied", retryable=False, detail="no actor tenant authorization"
            )
        try:
            authorize_operator_command(grant, command, actor_id=actor_id, tenant_id=self._tenant_id)
        except PermissionError as exc:
            raise OperatorCommandError(
                "authority_denied", retryable=False, detail=str(exc)
            ) from exc
        allowed_targets = OPERATOR_COMMAND_TARGETS.get(command["command_type"], frozenset())
        if command["target_record_type"] not in allowed_targets:
            raise OperatorCommandError(
                "validation_failed", retryable=False, detail="target type not allowed"
            )
        if command["command_type"] in _CANONICAL_MUTATION_COMMANDS:
            return self._dispatch_canonical_mutation(command, actor_id=actor_id, now=now)
        if command["command_type"] in _WORKFLOW_COMMANDS:
            return self._dispatch_workflow_command(command, actor_id=actor_id, now=now)
        raise OperatorCommandError(
            "validation_failed",
            retryable=False,
            detail="operator command semantics are unavailable",
        )

    def _dispatch_canonical_mutation(
        self, command: dict[str, Any], *, actor_id: str, now: datetime
    ) -> dict[str, Any]:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"operator-command:{self._tenant_id}:{command['idempotency_key']}",),
                )
                duplicate = self._existing_on(cursor, command["idempotency_key"])
                if duplicate is not None:
                    if duplicate["payload_digest"] != command["payload_digest"]:
                        raise OperatorCommandError(
                            "payload_mismatch",
                            retryable=False,
                            detail="idempotency key reused",
                        )
                    result = dict(cast(dict[str, Any], duplicate["result"]))
                    result["status"] = "duplicate"
                    self._connection.commit()
                    return result
                self._require_atomic_authority_on(cursor, command, actor_id, now)
                mutation = command["mutation"]
                kind = mutation["kind"]
                if kind == "correction":
                    corrected, replacement, correction = self._repository.apply_correction_on(
                        cursor,
                        mutation["correction_record"],
                        mutation["corrected_item_update"],
                        expected_corrected_version=int(command["expected_version"]),
                        raw_replacement=mutation.get("replacement_record"),
                    )
                    records = (
                        [corrected] + ([] if replacement is None else [replacement]) + [correction]
                    )
                    evidence_id = str(correction["id"])
                elif kind == "authorization_revocation":
                    updated = self._repository.save_on(
                        cursor,
                        mutation["authorization_update"],
                        expected_version=int(command["expected_version"]),
                    )
                    records = [updated]
                    evidence_id = str(updated["revocationEvidenceId"])
                elif kind == "approval_revocation":
                    prior, revoked = self._repository.supersede_on(
                        cursor,
                        mutation["prior_approval_update"],
                        mutation["revoked_approval_record"],
                        expected_prior_version=int(command["expected_version"]),
                    )
                    records = [prior, revoked]
                    evidence_id = str(revoked["id"])
                else:
                    prior, decided = self._repository.supersede_on(
                        cursor,
                        mutation["prior_approval_update"],
                        mutation["decided_approval_record"],
                        expected_prior_version=int(command["expected_version"]),
                    )
                    records = [prior, decided]
                    evidence_id = str(decided["id"])
                current_version = int(records[0]["version"])
                result = {
                    "message_type": "operator_command_result",
                    "schema_version": "operator-surface/1.1.0",
                    "command_id": command["command_id"],
                    "tenant_id": self._tenant_id,
                    "status": "applied",
                    "decided_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "decision_evidence_id": evidence_id,
                    "current_version": current_version,
                    "result_refs": [_ref(record) for record in records],
                }
                validate_record(result, "operator_surface")
                self._store_on(cursor, command, result)
        except OperatorCommandError:
            self._connection.rollback()
            raise
        except VersionConflict as exc:
            self._connection.rollback()
            raise OperatorCommandError("version_conflict", retryable=True, detail=str(exc)) from exc
        except PermissionError as exc:
            self._connection.rollback()
            raise OperatorCommandError(
                "authority_denied", retryable=False, detail=str(exc)
            ) from exc
        except (ContractViolation, ValueError, KeyError) as exc:
            self._connection.rollback()
            raise OperatorCommandError(
                "validation_failed", retryable=False, detail=str(exc)
            ) from exc
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return result

    def _dispatch_workflow_command(
        self, command: dict[str, Any], *, actor_id: str, now: datetime
    ) -> dict[str, Any]:
        mutation = command["mutation"]
        workflow_update = mutation["workflow_reference_update"]
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"operator-command:{self._tenant_id}:{command['idempotency_key']}",),
                )
                duplicate = self._existing_on(cursor, command["idempotency_key"])
                if duplicate is not None:
                    if duplicate["payload_digest"] != command["payload_digest"]:
                        raise OperatorCommandError(
                            "payload_mismatch", retryable=False, detail="idempotency key reused"
                        )
                    result = dict(cast(dict[str, Any], duplicate["result"]))
                    result["status"] = "duplicate"
                    self._connection.commit()
                    return result
                target = self._require_atomic_authority_on(cursor, command, actor_id, now)
                current_workflow = self._repository.load_current_on(
                    cursor, str(workflow_update["id"]), for_update=True
                )
                expected_workflow_version = int(mutation["workflow_reference_expected_version"])
                if (
                    current_workflow is None
                    or current_workflow["recordType"] != "WorkflowReference"
                    or int(current_workflow["version"]) != expected_workflow_version
                    or current_workflow["workflowId"] != workflow_update["workflowId"]
                    or current_workflow["runId"] != workflow_update["runId"]
                ):
                    raise VersionConflict("workflow reference changed before signal enqueue")
                updated = self._repository.save_on(
                    cursor,
                    workflow_update,
                    expected_version=expected_workflow_version,
                )
                result = {
                    "message_type": "operator_command_result",
                    "schema_version": "operator-surface/1.1.0",
                    "command_id": command["command_id"],
                    "tenant_id": self._tenant_id,
                    "status": "applied",
                    "decided_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "decision_evidence_id": mutation["signal_id"],
                    "current_version": int(updated["version"]),
                    "result_refs": [_ref(target), _ref(updated)],
                }
                validate_record(result, "operator_surface")
                self._store_on(cursor, command, result)
                cursor.execute(
                    """
                    INSERT INTO operator_workflow_outbox (
                        tenant_id, outbox_id, command_id, idempotency_key,
                        workflow_id, run_id, signal_name, signal_id, signal_payload, state
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                    """.strip(),
                    (
                        self._tenant_id,
                        f"outbox-{command['command_id']}",
                        command["command_id"],
                        command["idempotency_key"],
                        workflow_update["workflowId"],
                        workflow_update["runId"],
                        mutation["signal_name"],
                        mutation["signal_id"],
                        Jsonb(mutation["signal_payload"]),
                    ),
                )
        except OperatorCommandError:
            self._connection.rollback()
            raise
        except VersionConflict as exc:
            self._connection.rollback()
            raise OperatorCommandError("version_conflict", retryable=True, detail=str(exc)) from exc
        except PermissionError as exc:
            self._connection.rollback()
            raise OperatorCommandError(
                "authority_denied", retryable=False, detail=str(exc)
            ) from exc
        except (ContractViolation, ValueError, KeyError) as exc:
            self._connection.rollback()
            raise OperatorCommandError(
                "validation_failed", retryable=False, detail=str(exc)
            ) from exc
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return result

    async def dispatch_workflow_outbox(
        self, dispatcher: WorkflowSignalDispatcher, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Deliver pending signals and persist a typed receipt for each attempt."""
        if limit < 1:
            raise ValueError("limit must be positive")
        delivered: list[dict[str, Any]] = []
        for _ in range(limit):
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
                cursor.execute(
                    """
                    UPDATE operator_workflow_outbox
                    SET state = 'pending', failure_code = NULL, dispatching_at = NULL
                    WHERE tenant_id = %s
                      AND attempt < 8
                      AND (
                          (state = 'dispatching'
                           AND dispatching_at < clock_timestamp() - interval '5 minutes')
                          OR
                          (state = 'failed'
                           AND dispatched_at < clock_timestamp() - interval '5 minutes')
                      )
                    """.strip(),
                    (self._tenant_id,),
                )
                cursor.execute(
                    """
                    SELECT outbox_id, command_id, workflow_id, run_id, signal_name,
                           signal_id, signal_payload, attempt
                    FROM operator_workflow_outbox
                    WHERE tenant_id = %s AND state = 'pending'
                    ORDER BY created_at, outbox_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """.strip(),
                    (self._tenant_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    self._connection.commit()
                    break
                (
                    outbox_id,
                    command_id,
                    workflow_id,
                    run_id,
                    signal_name,
                    signal_id,
                    payload,
                    attempt,
                ) = row
                cursor.execute(
                    "UPDATE operator_workflow_outbox SET state = 'dispatching', attempt = attempt + 1, dispatching_at = clock_timestamp() WHERE tenant_id = %s AND outbox_id = %s",
                    (self._tenant_id, outbox_id),
                )
            self._connection.commit()
            try:
                provider_receipt_id = await dispatcher.dispatch(
                    workflow_id=str(workflow_id),
                    run_id=str(run_id),
                    signal_name=str(signal_name),
                    signal_id=str(signal_id),
                    payload=cast(dict[str, Any], payload),
                )
            except Exception as exc:
                self._record_signal_receipt(
                    str(outbox_id),
                    str(command_id),
                    str(workflow_id),
                    str(run_id),
                    str(signal_name),
                    attempt=int(attempt) + 1,
                    state="failed",
                    failure_code=type(exc).__name__,
                )
                continue
            self._record_signal_receipt(
                str(outbox_id),
                str(command_id),
                str(workflow_id),
                str(run_id),
                str(signal_name),
                attempt=int(attempt) + 1,
                state="delivered",
                provider_receipt_id=str(provider_receipt_id),
            )
            delivered.append({"outbox_id": str(outbox_id), "command_id": str(command_id)})
        return delivered

    def _record_signal_receipt(
        self,
        outbox_id: str,
        command_id: str,
        workflow_id: str,
        run_id: str,
        signal_name: str,
        *,
        attempt: int,
        state: str,
        provider_receipt_id: str | None = None,
        failure_code: str | None = None,
    ) -> None:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
                cursor.execute(
                    """
                    INSERT INTO operator_workflow_signal_receipts (
                        tenant_id, receipt_id, outbox_id, command_id, workflow_id, run_id,
                        signal_name, attempt, state, provider_receipt_id, failure_code
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """.strip(),
                    (
                        self._tenant_id,
                        f"receipt-{outbox_id}-{attempt}",
                        outbox_id,
                        command_id,
                        workflow_id,
                        run_id,
                        signal_name,
                        attempt,
                        state,
                        provider_receipt_id,
                        failure_code,
                    ),
                )
                cursor.execute(
                    "UPDATE operator_workflow_outbox SET state = %s, failure_code = %s, dispatching_at = NULL, dispatched_at = clock_timestamp() WHERE tenant_id = %s AND outbox_id = %s",
                    (
                        "dispatched" if state == "delivered" else "failed",
                        failure_code,
                        self._tenant_id,
                        outbox_id,
                    ),
                )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()

    def _require_atomic_authority_on(
        self,
        cursor: Any,
        command: dict[str, Any],
        actor_id: str,
        now: datetime,
    ) -> dict[str, Any]:
        cursor.execute(
            """
            SELECT payload FROM actor_tenant_authorizations_current
            WHERE tenant_id = %s AND actor_id = %s
            FOR SHARE
            """.strip(),
            (self._tenant_id, actor_id),
        )
        grant_rows = cursor.fetchall()
        if len(grant_rows) != 1:
            raise PermissionError("no unique current actor tenant authorization")
        grant = grant_rows[0][0]
        validate_authority_activation_fair_housing_semantics(grant, now=now)
        authorize_operator_command(grant, command, actor_id=actor_id, tenant_id=self._tenant_id)
        target = self._repository.load_current_on(
            cursor, str(command["target_record_id"]), for_update=True
        )
        if target is None:
            raise KeyError("target missing")
        if target["recordType"] != command["target_record_type"] or int(target["version"]) != int(
            command["expected_version"]
        ):
            raise VersionConflict("operator target changed before mutation")
        authority = command["authority"]
        policy = self._policy_repository.require_current_on(
            cursor, authority["policy_ref"], evaluated_at=now
        )
        matches = [
            rule
            for rule in policy["command_rules"]
            if rule["command_type"] == command["command_type"]
        ]
        if len(matches) != 1:
            raise PermissionError("operator policy has no unique command rule")
        rule = matches[0]
        if (
            rule["action_class"] != authority["action_class"]
            or command["target_record_type"] not in rule["target_record_types"]
            or authority["actor_type"] not in rule["actor_types"]
        ):
            raise PermissionError("operator policy rule does not authorize this command")
        return target

    def _existing_on(self, cursor: Any, idempotency_key: str) -> dict[str, Any] | None:
        cursor.execute(
            """
            SELECT payload_digest, result FROM operator_command_results
            WHERE tenant_id = %s AND idempotency_key = %s
            FOR SHARE
            """.strip(),
            (self._tenant_id, idempotency_key),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {"payload_digest": row[0], "result": row[1]}

    def _store_on(self, cursor: Any, command: dict[str, Any], result: dict[str, Any]) -> None:
        cursor.execute(
            """
            INSERT INTO operator_command_results (
                tenant_id, idempotency_key, payload_digest, command_id, result
            ) VALUES (%s, %s, %s, %s, %s)
            """.strip(),
            (
                self._tenant_id,
                command["idempotency_key"],
                command["payload_digest"],
                command["command_id"],
                Jsonb(result),
            ),
        )


def _ref(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": record["id"],
        "record_type": record["recordType"],
        "version": int(record["version"]),
        "status": record["status"],
    }


def command_payload_digest(command: dict[str, Any]) -> str:
    return operator_payload_digest(command)
