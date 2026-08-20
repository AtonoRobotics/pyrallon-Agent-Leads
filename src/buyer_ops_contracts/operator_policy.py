"""Versioned tenant-scoped OperatorPolicy persistence and revalidation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

from psycopg.types.json import Jsonb

from .canonical_repository import Connection, Cursor, VersionConflict
from .operator_contract import validate_operator_semantics
from .structural import validate_record


class OperatorPolicyRepository:
    def __init__(self, connection: Connection, *, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._connection = connection
        self._tenant_id = tenant_id

    def admit(
        self, policy: dict[str, Any], *, expected_version: int | None = None
    ) -> dict[str, Any]:
        validate_record(policy, "operator_surface")
        validate_operator_semantics(policy)
        if policy.get("message_type") != "operator_policy":
            raise ValueError("record must be an OperatorPolicy")
        if policy["tenant_id"] != self._tenant_id:
            raise ValueError("operator policy tenant mismatch")
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"operator-policy:{self._tenant_id}:{policy['policy_id']}",),
                )
                cursor.execute(
                    """
                    SELECT record_version FROM operator_policies_current
                    WHERE tenant_id = %s AND policy_id = %s FOR UPDATE
                    """.strip(),
                    (self._tenant_id, policy["policy_id"]),
                )
                row = cursor.fetchone()
                current = None if row is None else int(cast(int, row[0]))
                version = int(policy["record_version"])
                if current is None:
                    if expected_version is not None or version != 1:
                        raise VersionConflict("new operator policies must start at version 1")
                elif expected_version != current or version != current + 1:
                    raise VersionConflict("operator policy version conflict")
                cursor.execute(
                    """
                    INSERT INTO operator_policy_versions
                        (tenant_id, policy_id, record_version, policy)
                    VALUES (%s, %s, %s, %s)
                    """.strip(),
                    (
                        self._tenant_id,
                        policy["policy_id"],
                        version,
                        Jsonb(policy),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO operator_policies_current
                        (tenant_id, policy_id, record_version, status, policy)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, policy_id) DO UPDATE SET
                        record_version = EXCLUDED.record_version,
                        status = EXCLUDED.status,
                        policy = EXCLUDED.policy,
                        updated_at = clock_timestamp()
                    """.strip(),
                    (
                        self._tenant_id,
                        policy["policy_id"],
                        version,
                        policy["status"],
                        Jsonb(policy),
                    ),
                )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return policy

    def get_current(self, policy_id: str) -> dict[str, Any] | None:
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                policy = self.load_current_on(cursor, policy_id)
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return policy

    def load_current_on(self, cursor: Cursor, policy_id: str) -> dict[str, Any] | None:
        cursor.execute(
            """
            SELECT policy FROM operator_policies_current
            WHERE tenant_id = %s AND policy_id = %s FOR SHARE
            """.strip(),
            (self._tenant_id, policy_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        value = row[0]
        if isinstance(value, str):
            value = json.loads(value)
        return cast(dict[str, Any], value)

    def require_current_on(
        self,
        cursor: Cursor,
        reference: dict[str, Any],
        *,
        evaluated_at: datetime,
    ) -> dict[str, Any]:
        if reference.get("record_type") != "OperatorPolicy":
            raise PermissionError("operator policy reference type mismatch")
        policy = self.load_current_on(cursor, str(reference["record_id"]))
        if policy is None:
            raise PermissionError("operator policy unavailable")
        if (
            policy["record_version"] != reference["version"]
            or policy["tenant_id"] != self._tenant_id
            or policy["status"] != "active"
            or reference["status"] != "active"
        ):
            raise PermissionError("operator policy is not the referenced current version")
        active = evaluated_at.astimezone(UTC)
        starts = datetime.fromisoformat(policy["effective_from"].replace("Z", "+00:00"))
        ends = (
            None
            if "effective_to" not in policy
            else datetime.fromisoformat(policy["effective_to"].replace("Z", "+00:00"))
        )
        if starts > active or (ends is not None and ends <= active):
            raise PermissionError("operator policy is outside its effective interval")
        return policy

    def _set_tenant(self, cursor: Cursor) -> None:
        cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
