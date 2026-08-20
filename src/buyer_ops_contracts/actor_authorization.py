"""OPEN-025 ActorTenantAuthorization persistence and current projection."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from psycopg.types.json import Jsonb

from .authority_activation_fair_housing import validate_authority_activation_fair_housing_semantics
from .canonical_repository import Connection
from .structural import validate_record


class ActorTenantAuthorizationRepository:
    def __init__(self, connection: Connection, *, tenant_id: str | None = None) -> None:
        self._connection = connection
        self._tenant_id = tenant_id

    def save(self, record: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        validate_record(record, "authority_activation_fair_housing")
        if record.get("recordType") != "ActorTenantAuthorization":
            raise ValueError("only ActorTenantAuthorization can be admitted here")
        validate_authority_activation_fair_housing_semantics(record, now=now)
        if self._tenant_id and record["tenantId"] != self._tenant_id:
            raise ValueError("authorization tenant does not match repository tenant")
        tenant_id = str(record["tenantId"])
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                cursor.execute(
                    """
                    INSERT INTO actor_tenant_authorization_versions (
                        tenant_id, record_id, authorization_version, actor_id, status, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """.strip(),
                    (
                        tenant_id,
                        record["recordId"],
                        int(record["authorizationVersion"]),
                        record["actorId"],
                        record["status"],
                        Jsonb(record),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO actor_tenant_authorizations_current (
                        tenant_id, record_id, authorization_version, actor_id, status,
                        payload, effective_at, expires_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, record_id) DO UPDATE SET
                        authorization_version = EXCLUDED.authorization_version,
                        actor_id = EXCLUDED.actor_id,
                        status = EXCLUDED.status,
                        payload = EXCLUDED.payload,
                        effective_at = EXCLUDED.effective_at,
                        expires_at = EXCLUDED.expires_at
                    """.strip(),
                    (
                        tenant_id,
                        record["recordId"],
                        int(record["authorizationVersion"]),
                        record["actorId"],
                        record["status"],
                        Jsonb(record),
                        record["effectiveAt"],
                        record["expiresAt"],
                    ),
                )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return record

    def list_current_for_actor(
        self, actor_id: str, *, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        if not actor_id:
            return []
        current = (now or datetime.now(UTC)).astimezone(UTC)
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.actor_id', %s, true)", (actor_id,))
                cursor.execute(
                    """
                    SELECT payload FROM actor_tenant_authorizations_current
                    WHERE actor_id = %s
                    ORDER BY tenant_id, record_id
                    """.strip(),
                    (actor_id,),
                )
                rows = cursor.fetchall()
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        admitted: list[dict[str, Any]] = []
        for row in rows:
            record = cast(dict[str, Any], row[0])
            if record.get("status") != "active":
                continue
            try:
                validate_authority_activation_fair_housing_semantics(record, now=current)
            except Exception:
                continue
            admitted.append(record)
        return admitted

    def current(self, actor_id: str, *, now: datetime | None = None) -> dict[str, Any] | None:
        if not self._tenant_id:
            raise ValueError("tenant_id is required")
        grants = [
            item
            for item in self.list_current_for_actor(actor_id, now=now)
            if item.get("tenantId") == self._tenant_id
        ]
        if not grants:
            return None
        if len(grants) != 1:
            raise PermissionError("ambiguous current actor tenant authorization")
        return grants[0]


def authorize_operator_command(
    grant: dict[str, Any] | None,
    command: dict[str, Any],
    *,
    actor_id: str,
    tenant_id: str,
) -> None:
    """Enforce the exact bindings published for OPEN-025."""
    if grant is None:
        raise PermissionError("no current actor tenant authorization")
    authority = command["authority"]
    if grant["actorId"] != actor_id or grant["tenantId"] != tenant_id:
        raise PermissionError("actor or tenant authorization mismatch")
    if command["tenant_id"] != tenant_id:
        raise PermissionError("command tenant mismatch")
    if command["command_type"] not in grant["allowedCommands"]:
        raise PermissionError("command is not allowed")
    scopes = set(grant["recordScopes"])
    requested_scopes = {
        str(command["target_record_type"]),
        str(authority["resource_type"]),
        str(authority["resource_id"]),
    }
    if scopes.isdisjoint(requested_scopes):
        raise PermissionError("record scope is not allowed")
    authorization_refs = authority["authorization_refs"]
    if len(authorization_refs) != 1:
        raise PermissionError("one exact actor tenant authorization reference is required")
    reference = authorization_refs[0]
    if (
        reference["record_type"] != "ActorTenantAuthorization"
        or reference["record_id"] != grant["recordId"]
        or int(reference["version"]) != int(grant["authorizationVersion"])
        or reference["status"] != grant["status"]
    ):
        raise PermissionError("authorization reference is stale or inexact")
    if authority["policy_ref"]["record_id"] != grant["policyVersion"]:
        raise PermissionError("authorization policy version mismatch")
