"""Tenant-scoped append-only persistence for provider e-signature operations."""

from __future__ import annotations

import json
from typing import Any, cast

from .canonical_repository import Connection, TenantIsolationViolation


class ESignatureOperationRepository:
    def __init__(self, connection: Connection, *, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._connection = connection
        self._tenant_id = tenant_id

    def latest(self, *, agreement_id: str) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
            cursor.execute(
                """
                SELECT payload FROM esignature_operation_records
                WHERE tenant_id = %s AND agreement_id = %s
                ORDER BY recorded_at DESC LIMIT 1
                """.strip(),
                (self._tenant_id, agreement_id),
            )
            row = cursor.fetchone()
        self._connection.commit()
        return None if row is None else cast(dict[str, Any], row[0])

    def append(self, *, operation_id: str, record: dict[str, Any]) -> dict[str, Any]:
        if record.get("tenantId") != self._tenant_id:
            raise TenantIsolationViolation("operation tenant does not match repository tenant")
        for key in ("agreementId", "state"):
            if not isinstance(record.get(key), str) or not record[key]:
                raise ValueError(f"operation {key} is required")
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
                cursor.execute(
                    """
                    INSERT INTO esignature_operation_records
                        (tenant_id, operation_id, agreement_id, provider_envelope_id,
                         state, provider_status, payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (tenant_id, operation_id) DO NOTHING
                    """.strip(),
                    (
                        self._tenant_id,
                        operation_id,
                        record["agreementId"],
                        record.get("providerEnvelopeId"),
                        record["state"],
                        record.get("providerStatus"),
                        json.dumps(record, sort_keys=True, separators=(",", ":")),
                    ),
                )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return record
