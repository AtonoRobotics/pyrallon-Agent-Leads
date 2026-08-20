"""Read-only PostgreSQL access for admitted derived contract families."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, cast

from .canonical_repository import Connection
from .registry import ContractRegistry
from .structural import validate_record

ConnectionFactory = Callable[[], AbstractContextManager[Connection]]

_IDENTITY_FIELDS = {
    "qualification_policy": "policyId",
    "qualification_input_set": "inputSetId",
    "next_question_decision": "decisionId",
    "readiness_decision": "decisionId",
    "calendar_provider_binding": "bindingId",
    "availability_policy": "policyId",
    "calendar_snapshot": "snapshotId",
    "slot_set": "slotSetId",
    "booking_command": "commandId",
    "booking_result": "resultId",
    "booking_reconciliation": "reconciliationId",
}
_VERSIONED_MESSAGE_TYPES = {
    "qualification_policy",
    "calendar_provider_binding",
    "availability_policy",
}


class DerivedContractReader:
    """Read stored family records without activating a production writer."""

    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        tenant_id: str,
        registry: ContractRegistry | None = None,
    ) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._connection_factory = connection_factory
        self._tenant_id = tenant_id
        self._registry = registry or ContractRegistry()

    def get(
        self,
        *,
        contract_family: str,
        message_type: str,
        record_id: str,
        record_version: int,
    ) -> dict[str, Any] | None:
        """Return one exact version after registry and envelope validation."""

        self._registry.get(contract_family)
        if not message_type or not record_id or record_version < 1:
            raise ValueError("message_type, record_id, and positive record_version are required")
        with self._connection_factory() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('app.tenant_id', %s, true)",
                        (self._tenant_id,),
                    )
                    cursor.execute(
                        """
                        SELECT contract_family, message_type, record_id, record_version,
                               schema_version, payload
                        FROM derived_contract_records
                        WHERE tenant_id = %s
                          AND contract_family = %s
                          AND message_type = %s
                          AND record_id = %s
                          AND record_version = %s
                        """.strip(),
                        (
                            self._tenant_id,
                            contract_family,
                            message_type,
                            record_id,
                            record_version,
                        ),
                    )
                    row = cursor.fetchone()
                    if row is not None:
                        payload = cast(dict[str, Any], row[5])
                        validate_record(payload, contract_family, self._registry)
                        self._validate_envelope(
                            row=row,
                            payload=payload,
                            contract_family=contract_family,
                            message_type=message_type,
                            record_id=record_id,
                            record_version=record_version,
                        )
            except Exception:
                connection.rollback()
                raise
            connection.commit()
        return None if row is None else payload

    def _validate_envelope(
        self,
        *,
        row: tuple[object, ...],
        payload: dict[str, Any],
        contract_family: str,
        message_type: str,
        record_id: str,
        record_version: int,
    ) -> None:
        identity_field = _IDENTITY_FIELDS.get(message_type)
        if identity_field is None:
            raise RuntimeError("stored derived-contract message type is unsupported")
        expected_envelope = (
            contract_family,
            message_type,
            record_id,
            record_version,
            payload["schemaVersion"],
        )
        payload_version = payload.get("version")
        version_matches = (
            payload_version == record_version
            if message_type in _VERSIONED_MESSAGE_TYPES
            else payload_version is None and record_version == 1
        )
        if (
            tuple(row[:5]) != expected_envelope
            or payload["messageType"] != message_type
            or payload[identity_field] != record_id
            or not version_matches
        ):
            raise RuntimeError("stored derived-contract envelope mismatch")
        if payload["tenantId"] != self._tenant_id:
            raise RuntimeError("stored derived-contract tenant mismatch")
