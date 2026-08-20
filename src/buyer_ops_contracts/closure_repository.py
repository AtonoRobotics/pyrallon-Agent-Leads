"""Append-only persistence and unambiguous current projection for closure records."""

from __future__ import annotations

import json
from typing import Any, cast

from psycopg.types.json import Jsonb

from .canonical_repository import Connection
from .closure import validate_closure_semantics
from .structural import validate_record


class ClosureVersionConflict(RuntimeError):
    pass


def closure_identity_key(record: dict[str, Any]) -> str:
    record_type = record["recordType"]
    fields_by_type = {
        "ExternalMessageIdentity": (
            "connectorId",
            "providerAccountRef",
            "externalMessageId",
        ),
        "CapabilityInventory": ("connectorId",),
        "EffectDraftPreview": ("recordId",),
        "ContextSourceFreshness": ("sourceRecordId",),
        "OutputClassMapping": ("actionClass", "policyVersion"),
        "MetricDefinition": ("metricId",),
        "MetricObservation": ("recordId",),
        "ReleaseEvidence": ("gateId", "scope", "releaseDigest"),
        "AccessibilityEvidence": ("surface", "buildDigest", "releaseDigest"),
    }
    fields = fields_by_type.get(record_type)
    if fields is None:
        raise ValueError(f"unsupported closure record type: {record_type}")
    return json.dumps([record[field] for field in fields], separators=(",", ":"))


class PostgresClosureRepository:
    def __init__(self, connection: Connection, *, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._connection = connection
        self._tenant_id = tenant_id

    def save(self, record: dict[str, Any]) -> dict[str, Any]:
        validate_record(record, "closure")
        validate_closure_semantics(record)
        if record["tenantId"] != self._tenant_id:
            raise ValueError("closure record tenant does not match repository tenant")
        identity_key = closure_identity_key(record)
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"closure:{self._tenant_id}:{record['recordType']}:{identity_key}",),
                )
                cursor.execute(
                    """
                    SELECT record_id, record_version, payload
                    FROM closure_records_current
                    WHERE tenant_id = %s AND record_type = %s AND identity_key = %s
                    FOR UPDATE
                    """.strip(),
                    (self._tenant_id, record["recordType"], identity_key),
                )
                current = cursor.fetchone()
                self._validate_successor(record, current)
                cursor.execute(
                    """
                    INSERT INTO closure_records (
                        tenant_id, record_id, record_version, record_type, identity_key,
                        status, payload, observed_at, effective_from, effective_to, expires_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """.strip(),
                    (
                        self._tenant_id,
                        record["recordId"],
                        record["recordVersion"],
                        record["recordType"],
                        identity_key,
                        record["status"],
                        Jsonb(record),
                        record["observedAt"],
                        record["effectiveFrom"],
                        record.get("effectiveTo"),
                        record.get("expiresAt"),
                    ),
                )
                if record["status"] == "current":
                    cursor.execute(
                        """
                        INSERT INTO closure_records_current (
                            tenant_id, record_type, identity_key, record_id, record_version, payload
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (tenant_id, record_type, identity_key) DO UPDATE SET
                            record_id = EXCLUDED.record_id,
                            record_version = EXCLUDED.record_version,
                            payload = EXCLUDED.payload
                        """.strip(),
                        (
                            self._tenant_id,
                            record["recordType"],
                            identity_key,
                            record["recordId"],
                            record["recordVersion"],
                            Jsonb(record),
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        DELETE FROM closure_records_current
                        WHERE tenant_id = %s AND record_type = %s AND identity_key = %s
                        """.strip(),
                        (self._tenant_id, record["recordType"], identity_key),
                    )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return record

    def current(self, record_type: str, identity_key: str) -> dict[str, Any] | None:
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    SELECT payload FROM closure_records_current
                    WHERE tenant_id = %s AND record_type = %s AND identity_key = %s
                    """.strip(),
                    (self._tenant_id, record_type, identity_key),
                )
                row = cursor.fetchone()
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return None if row is None else cast(dict[str, Any], row[0])

    def current_inventory(self, tenant_id: str, connector_id: str) -> dict[str, Any] | None:
        if tenant_id != self._tenant_id:
            raise ValueError("inventory tenant does not match repository tenant")
        identity_key = json.dumps([connector_id], separators=(",", ":"))
        return self.current("CapabilityInventory", identity_key)

    @staticmethod
    def _validate_successor(record: dict[str, Any], current: tuple[object, ...] | None) -> None:
        if current is None:
            if record["recordVersion"] != 1 or record.get("supersedesRecordId") is not None:
                raise ClosureVersionConflict("new closure identity must start at version one")
            return
        current_id, current_version, _ = current
        if record["recordVersion"] != int(cast(int, current_version)) + 1:
            raise ClosureVersionConflict("closure version must advance by exactly one")
        if record.get("supersedesRecordId") != current_id:
            raise ClosureVersionConflict("closure successor must identify current predecessor")

    def _set_tenant(self, cursor: Any) -> None:
        cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
