"""Fail-closed operator command admission for web, iOS, and reconnect queues."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import Any, Protocol

from psycopg.types.json import Jsonb

from .canonical_repository import Connection
from .errors import ContractViolation
from .operator_contract import OPERATOR_COMMAND_TARGETS, validate_operator_semantics
from .structural import validate_record


class OperatorRejected(RuntimeError):
    def __init__(self, code: str, *, current_version: int | None = None) -> None:
        self.code = code
        self.current_version = current_version
        super().__init__(code)


class OperatorAuthorityEvaluator(Protocol):
    """Authenticate actor and re-read every authorization and effective policy version."""

    def authorize(self, command: dict[str, Any]) -> bool: ...


class OperatorTargetReader(Protocol):
    def current_version(self, command: dict[str, Any]) -> int: ...


class OperatorIdempotencyRepository(Protocol):
    def lookup(self, tenant_id: str, key: str) -> tuple[str, dict[str, Any]] | None: ...

    def record(self, tenant_id: str, key: str, digest: str, result: dict[str, Any]) -> None: ...


class OperatorCommandExecutor(Protocol):
    def execute(self, command: dict[str, Any]) -> dict[str, Any]: ...


class OperatorCommandService:
    def __init__(
        self,
        authority: OperatorAuthorityEvaluator,
        targets: OperatorTargetReader,
        idempotency: OperatorIdempotencyRepository,
        executor: OperatorCommandExecutor,
    ) -> None:
        self._authority = authority
        self._targets = targets
        self._idempotency = idempotency
        self._executor = executor

    def execute(
        self,
        command: dict[str, Any],
        *,
        evaluated_at: datetime,
    ) -> dict[str, Any]:
        try:
            validate_record(command, "operator_surface")
            validate_operator_semantics(command)
        except ContractViolation as exc:
            code = (
                "payload_mismatch"
                if any(item.code == "OPERATOR_PAYLOAD_DIGEST_MISMATCH" for item in exc.violations)
                else "validation_failed"
            )
            raise OperatorRejected(code) from exc
        if command.get("message_type") != "operator_command":
            raise OperatorRejected("validation_failed")
        self._validate_command_target(command)
        self._validate_time(command, evaluated_at)
        if not self._authority.authorize(command):
            raise OperatorRejected("authority_denied")

        prior = self._idempotency.lookup(command["tenant_id"], command["idempotency_key"])
        if prior is not None:
            prior_digest, prior_result = prior
            if not hmac.compare_digest(prior_digest, command["payload_digest"]):
                raise OperatorRejected("payload_mismatch")
            duplicate = {**prior_result, "status": "duplicate"}
            validate_record(duplicate, "operator_surface")
            return duplicate

        current_version = self._targets.current_version(command)
        if current_version != command["expected_version"]:
            raise OperatorRejected("version_conflict", current_version=current_version)
        result = self._executor.execute(command)
        validate_record(result, "operator_surface")
        if (
            result.get("message_type") != "operator_command_result"
            or result.get("command_id") != command["command_id"]
            or result.get("tenant_id") != command["tenant_id"]
        ):
            raise OperatorRejected("validation_failed")
        self._idempotency.record(
            command["tenant_id"],
            command["idempotency_key"],
            command["payload_digest"],
            result,
        )
        return result

    @staticmethod
    def _validate_command_target(command: dict[str, Any]) -> None:
        allowed = OPERATOR_COMMAND_TARGETS.get(command["command_type"], frozenset())
        if command["target_record_type"] not in allowed:
            raise OperatorRejected("validation_failed")

    @staticmethod
    def _validate_time(command: dict[str, Any], evaluated_at: datetime) -> None:
        now = evaluated_at.astimezone(UTC)
        issued = _timestamp(command["issued_at"])
        expires = _timestamp(command["expires_at"])
        if expires <= issued or now < issued or now >= expires:
            raise OperatorRejected("approval_expired")


class PostgresOperatorIdempotencyRepository:
    """Persist the first bound result; later equal replays read it without mutation."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def lookup(self, tenant_id: str, key: str) -> tuple[str, dict[str, Any]] | None:
        if not tenant_id or not key:
            raise ValueError("tenant and idempotency key are required")
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor, tenant_id)
                cursor.execute(
                    """
                    SELECT payload_digest, result FROM operator_command_results
                    WHERE tenant_id = %s AND idempotency_key = %s
                    """.strip(),
                    (tenant_id, key),
                )
                row = cursor.fetchone()
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        if row is None:
            return None
        result = row[1]
        if not isinstance(result, dict):
            raise RuntimeError("operator command result is not an object")
        return str(row[0]), result

    def record(self, tenant_id: str, key: str, digest: str, result: dict[str, Any]) -> None:
        validate_record(result, "operator_surface")
        if result.get("message_type") != "operator_command_result":
            raise ValueError("result must be an operator command result")
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor, tenant_id)
                cursor.execute(
                    """
                    INSERT INTO operator_command_results (
                        tenant_id, idempotency_key, payload_digest, command_id, result
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                    RETURNING payload_digest
                    """.strip(),
                    (tenant_id, key, digest, result["command_id"], Jsonb(result)),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute(
                        """
                        SELECT payload_digest FROM operator_command_results
                        WHERE tenant_id = %s AND idempotency_key = %s
                        """.strip(),
                        (tenant_id, key),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise RuntimeError("operator idempotency result disappeared")
                    if not hmac.compare_digest(str(row[0]), digest):
                        raise OperatorRejected("payload_mismatch")
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()

    @staticmethod
    def _set_tenant(cursor: Any, tenant_id: str) -> None:
        cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise OperatorRejected("validation_failed")
    return parsed.astimezone(UTC)
