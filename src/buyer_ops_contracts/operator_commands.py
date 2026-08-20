"""Dispatch operator-surface/1.1.0 OperatorCommand against canonical + Temporal."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, cast

from psycopg.types.json import Jsonb

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
        "correct_replace",
        "correct_invalidate",
        "revoke_authorization",
        "revoke_approval",
    }
)


class WorkflowOperator(Protocol):
    def pause(self, *, tenant_id: str, journey_id: str, workflow_id: str) -> None: ...

    def resume(self, *, tenant_id: str, journey_id: str, workflow_id: str) -> None: ...

    def request_reconciliation(
        self, *, tenant_id: str, journey_id: str, event_id: str, canonical_version: int
    ) -> None: ...


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
        workflow: WorkflowOperator | None = None,
        policy_repository: OperatorPolicyRepository | None = None,
    ) -> None:
        self._connection = connection
        self._repository = repository
        self._tenant_id = tenant_id
        self._workflow = workflow
        self._policy_repository = policy_repository or OperatorPolicyRepository(
            connection, tenant_id=tenant_id
        )

    def dispatch(self, command: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        validate_record(command, "operator_surface")
        validate_operator_semantics(command)
        if command.get("message_type") != "operator_command":
            raise OperatorCommandError("validation_failed", retryable=False, detail="not a command")
        if command["tenant_id"] != self._tenant_id:
            raise OperatorCommandError(
                "authority_denied", retryable=False, detail="tenant mismatch"
            )
        now = datetime.now(UTC)
        issued = datetime.fromisoformat(command["issued_at"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(command["expires_at"].replace("Z", "+00:00"))
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
        duplicate = self._existing(command["idempotency_key"])
        if duplicate is not None:
            if duplicate["payload_digest"] != command["payload_digest"]:
                raise OperatorCommandError(
                    "payload_mismatch", retryable=False, detail="idempotency key reused"
                )
            return cast(dict[str, Any], duplicate["result"])
        self._assert_authority(command, actor_id, now)
        target = self._repository.get(command["target_record_id"])
        if target is None:
            raise OperatorCommandError(
                "evidence_unavailable", retryable=False, detail="target missing"
            )
        if target.get("recordType") != command["target_record_type"]:
            raise OperatorCommandError(
                "validation_failed", retryable=False, detail="target type mismatch"
            )
        if int(target["version"]) != int(command["expected_version"]):
            raise OperatorCommandError(
                "version_conflict", retryable=True, detail="expected_version mismatch"
            )
        bound = operator_payload_digest(command)
        if bound != command["payload_digest"]:
            raise OperatorCommandError(
                "payload_mismatch", retryable=False, detail="payload digest mismatch"
            )
        try:
            result_refs, evidence_id, status = self._apply(command, target, now)
        except VersionConflict as exc:
            raise OperatorCommandError("version_conflict", retryable=True, detail=str(exc)) from exc
        except ContractViolation as exc:
            raise OperatorCommandError(
                "validation_failed", retryable=False, detail=str(exc)
            ) from exc
        current = self._repository.get(command["target_record_id"])
        current_version = int(current["version"]) if current else int(command["expected_version"])
        result = {
            "message_type": "operator_command_result",
            "schema_version": "operator-surface/1.1.0",
            "command_id": command["command_id"],
            "tenant_id": self._tenant_id,
            "status": status,
            "decided_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "decision_evidence_id": evidence_id,
            "current_version": current_version,
            "result_refs": result_refs,
        }
        validate_record(result, "operator_surface")
        self._store(command, result)
        return result

    def _dispatch_canonical_mutation(
        self, command: dict[str, Any], *, actor_id: str, now: datetime
    ) -> dict[str, Any]:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
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
                else:
                    prior, revoked = self._repository.supersede_on(
                        cursor,
                        mutation["prior_approval_update"],
                        mutation["revoked_approval_record"],
                        expected_prior_version=int(command["expected_version"]),
                    )
                    records = [prior, revoked]
                    evidence_id = str(revoked["id"])
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

    def _assert_authority(self, command: dict[str, Any], actor_id: str, now: datetime) -> None:
        authority = command["authority"]
        policy_ref = authority["policy_ref"]
        policy = self._policy_repository.get_current(str(policy_ref["record_id"]))
        if policy is None:
            raise OperatorCommandError(
                "policy_denied", retryable=False, detail="operator policy missing"
            )
        if (
            policy["record_version"] != policy_ref["version"]
            or policy["status"] != "active"
            or policy_ref["status"] != "active"
        ):
            raise OperatorCommandError(
                "policy_denied", retryable=False, detail="operator policy version mismatch"
            )
        starts = datetime.fromisoformat(policy["effective_from"].replace("Z", "+00:00"))
        ends = (
            None
            if "effective_to" not in policy
            else datetime.fromisoformat(policy["effective_to"].replace("Z", "+00:00"))
        )
        if starts > now or (ends is not None and ends <= now):
            raise OperatorCommandError(
                "policy_denied", retryable=False, detail="operator policy is not effective"
            )
        matches = [
            rule
            for rule in policy["command_rules"]
            if rule["command_type"] == command["command_type"]
        ]
        if len(matches) != 1:
            raise OperatorCommandError(
                "policy_denied", retryable=False, detail="operator command rule missing"
            )
        rule = matches[0]
        if (
            rule["action_class"] != authority["action_class"]
            or command["target_record_type"] not in rule["target_record_types"]
            or authority["actor_type"] not in rule["actor_types"]
        ):
            raise OperatorCommandError(
                "policy_denied", retryable=False, detail="operator command rule mismatch"
            )

    def _apply(
        self, command: dict[str, Any], target: dict[str, Any], now: datetime
    ) -> tuple[list[dict[str, Any]], str, str]:
        command_type = command["command_type"]
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        if command_type in {"approve", "deny"}:
            if target.get("recordType") != "Approval":
                raise OperatorCommandError(
                    "validation_failed", retryable=False, detail="not an Approval"
                )
            next_record = {
                **target,
                "version": int(target["version"]) + 1,
                "updatedAt": stamp,
                "decision": "approved" if command_type == "approve" else "denied",
                "reason": command["reason"],
            }
            saved = self._repository.save(next_record, expected_version=int(target["version"]))
            return [_ref(saved)], saved["id"], "applied"
        if command_type == "revoke_approval":
            if target.get("recordType") != "Approval":
                raise OperatorCommandError(
                    "validation_failed", retryable=False, detail="not an Approval"
                )
            next_record = {
                **target,
                "version": int(target["version"]) + 1,
                "updatedAt": stamp,
                "decision": "revoked",
                "reason": command["reason"],
            }
            saved = self._repository.save(next_record, expected_version=int(target["version"]))
            return [_ref(saved)], saved["id"], "applied"
        if command_type == "revoke_authorization":
            if target.get("recordType") != "Authorization":
                raise OperatorCommandError(
                    "validation_failed", retryable=False, detail="not an Authorization"
                )
            next_record = {
                **target,
                "version": int(target["version"]) + 1,
                "updatedAt": stamp,
                "authorizationState": "revoked",
                "revokedAt": stamp,
            }
            saved = self._repository.save(next_record, expected_version=int(target["version"]))
            return [_ref(saved)], saved["id"], "applied"
        if command_type in {"correct_replace", "correct_invalidate"}:
            if target.get("recordType") not in {"Assertion", "VerifiedFact", "Inference", "Memory"}:
                raise OperatorCommandError(
                    "validation_failed", retryable=False, detail="not an epistemic item"
                )
            state_field = {
                "Assertion": "assertionState",
                "VerifiedFact": "factState",
                "Inference": "inferenceState",
                "Memory": "memoryState",
            }[target["recordType"]]
            next_record = {
                **target,
                "version": int(target["version"]) + 1,
                "updatedAt": stamp,
                state_field: "invalidated"
                if command_type == "correct_invalidate"
                else "superseded",
            }
            saved = self._repository.save(next_record, expected_version=int(target["version"]))
            return [_ref(saved)], saved["id"], "applied"
        if command_type in {"pause_workflow", "resume_workflow"}:
            if target.get("recordType") != "WorkflowReference":
                raise OperatorCommandError(
                    "validation_failed", retryable=False, detail="not a WorkflowReference"
                )
            if self._workflow is None:
                raise OperatorCommandError(
                    "workflow_conflict", retryable=True, detail="temporal worker unavailable"
                )
            if command_type == "pause_workflow":
                self._workflow.pause(
                    tenant_id=self._tenant_id,
                    journey_id=command["journey_id"],
                    workflow_id=str(target["workflowId"]),
                )
                execution = "waiting"
            else:
                self._workflow.resume(
                    tenant_id=self._tenant_id,
                    journey_id=command["journey_id"],
                    workflow_id=str(target["workflowId"]),
                )
                execution = "running"
            next_record = {
                **target,
                "version": int(target["version"]) + 1,
                "updatedAt": stamp,
                "executionState": execution,
            }
            saved = self._repository.save(next_record, expected_version=int(target["version"]))
            return [_ref(saved)], saved["id"], "applied"
        if command_type == "request_reconciliation":
            if self._workflow is None:
                raise OperatorCommandError(
                    "workflow_conflict", retryable=True, detail="temporal worker unavailable"
                )
            self._workflow.request_reconciliation(
                tenant_id=self._tenant_id,
                journey_id=command["journey_id"],
                event_id=command["command_id"],
                canonical_version=int(target["version"]),
            )
            return [_ref(target)], target["id"], "applied"
        raise OperatorCommandError("validation_failed", retryable=False, detail="unknown command")

    def _existing(self, idempotency_key: str) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
            result = self._existing_on(cursor, idempotency_key)
        self._connection.commit()
        return result

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

    def _store(self, command: dict[str, Any], result: dict[str, Any]) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
            self._store_on(cursor, command, result)
        self._connection.commit()

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
