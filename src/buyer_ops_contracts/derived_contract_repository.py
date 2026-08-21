"""PostgreSQL access primitives for admitted derived contract families."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, cast

from .canonical_repository import Connection, TenantIsolationViolation
from .contract_acceptance import (
    ContractSemanticError,
    derive_qualification_decisions,
    derive_slot_set,
    validate_booking_command,
    validate_booking_result_context,
    validate_calendar_snapshot,
    validate_qualification_decisions,
    validate_reconciliation,
    validate_slot_set_context,
)
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


class SlotSetRepository:
    """Append a caller-supplied validated SlotSet without deriving or activating it."""

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

    def append_slot_set(
        self,
        *,
        policy: dict[str, Any],
        readiness: dict[str, Any],
        binding: dict[str, Any],
        snapshot: dict[str, Any],
        slot_set: dict[str, Any],
    ) -> None:
        availability_records = (policy, binding, snapshot, slot_set)
        for record in availability_records:
            validate_record(record, "availability_booking", self._registry)
        validate_record(readiness, "qualification_readiness", self._registry)
        if (
            tuple(record["messageType"] for record in availability_records)
            != (
                "availability_policy",
                "calendar_provider_binding",
                "calendar_snapshot",
                "slot_set",
            )
            or readiness["messageType"] != "readiness_decision"
        ):
            raise ValueError("SlotSet records do not have the required message types")
        if any(
            record["tenantId"] != self._tenant_id for record in (*availability_records, readiness)
        ):
            raise TenantIsolationViolation("record tenant does not match repository tenant")
        validate_slot_set_context(
            slot_set,
            policy=policy,
            readiness=readiness,
            binding=binding,
            snapshot=snapshot,
        )

        with self._connection_factory() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('app.tenant_id', %s, true)",
                        (self._tenant_id,),
                    )
                    cursor.execute(
                        """
                        INSERT INTO derived_contract_records (
                            tenant_id, contract_family, message_type, record_id,
                            record_version, schema_version, payload
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                        """.strip(),
                        (
                            self._tenant_id,
                            "availability_booking",
                            "slot_set",
                            slot_set["slotSetId"],
                            1,
                            slot_set["schemaVersion"],
                            json.dumps(
                                slot_set,
                                sort_keys=True,
                                separators=(",", ":"),
                                ensure_ascii=False,
                            ),
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def append_calendar_snapshot(self, *, snapshot: dict[str, Any]) -> None:
        """Append one provider-observed snapshot before availability is derived."""
        validate_record(snapshot, "availability_booking", self._registry)
        if snapshot.get("messageType") != "calendar_snapshot":
            raise ValueError("calendar snapshot has an unexpected message type")
        if snapshot.get("tenantId") != self._tenant_id:
            raise TenantIsolationViolation(
                "calendar snapshot tenantId does not match repository tenant"
            )
        validate_calendar_snapshot(snapshot)
        with self._connection_factory() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,)
                    )
                    cursor.execute(
                        """
                        INSERT INTO derived_contract_records
                            (tenant_id, contract_family, message_type, record_id,
                             record_version, schema_version, payload)
                        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                        ON CONFLICT (tenant_id, contract_family, message_type, record_id, record_version)
                        DO NOTHING
                        """.strip(),
                        (
                            self._tenant_id,
                            "availability_booking",
                            "calendar_snapshot",
                            snapshot["snapshotId"],
                            1,
                            snapshot["schemaVersion"],
                            json.dumps(snapshot, sort_keys=True, separators=(",", ":")),
                        ),
                    )
            except Exception:
                connection.rollback()
                raise
            connection.commit()

    def derive_and_append_slot_set(
        self,
        *,
        policy: dict[str, Any],
        readiness: dict[str, Any],
        binding: dict[str, Any],
        snapshot: dict[str, Any],
        derived_at: datetime,
        principal_id: str,
        location_options: tuple[tuple[str, tuple[str, ...]], ...],
        blocked_intervals: tuple[dict[str, str], ...] = (),
    ) -> dict[str, Any]:
        """Derive and atomically append a SlotSet from explicit owner inputs."""

        slot_set = derive_slot_set(
            policy,
            readiness,
            binding,
            snapshot,
            derived_at=derived_at,
            principal_id=principal_id,
            location_options=location_options,
            blocked_intervals=blocked_intervals,
        )
        self.append_slot_set(
            policy=policy,
            readiness=readiness,
            binding=binding,
            snapshot=snapshot,
            slot_set=slot_set,
        )
        return slot_set


class BookingOutcomeRepository:
    """Append validated booking outcomes without invoking or activating provider effects."""

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

    def append_booking_result(
        self,
        *,
        command: dict[str, Any],
        binding: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        records = (command, binding, result)
        self._validate_records(
            records,
            expected_types=("booking_command", "calendar_provider_binding", "booking_result"),
        )
        validate_booking_command(command)
        validate_booking_result_context(command=command, binding=binding, result=result)
        self._append(result, identity_field="resultId")

    def append_booking_command(self, *, command: dict[str, Any]) -> str:
        """Append one command, enforcing tenant scope and idempotency before dispatch."""
        validate_record(command, "availability_booking", self._registry)
        if command.get("messageType") != "booking_command":
            raise ValueError("booking command has an unexpected message type")
        if command.get("tenantId") != self._tenant_id:
            raise TenantIsolationViolation(
                "booking command tenantId does not match repository tenant"
            )
        validate_booking_command(command)
        with self._connection_factory() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('app.tenant_id', %s, true)",
                        (self._tenant_id,),
                    )
                    cursor.execute(
                        """
                        SELECT payload
                        FROM derived_contract_records
                        WHERE tenant_id = %s
                          AND contract_family = 'availability_booking'
                          AND message_type = 'booking_command'
                          AND payload->>'idempotencyKey' = %s
                        FOR SHARE
                        """.strip(),
                        (self._tenant_id, command["idempotencyKey"]),
                    )
                    existing = cursor.fetchone()
                    if existing is not None:
                        prior = cast(dict[str, Any], existing[0])
                        if prior.get("payloadDigest") != command.get("payloadDigest"):
                            raise ContractSemanticError("idempotency_key_payload_conflict")
                        connection.commit()
                        return "duplicate"
                    cursor.execute(
                        """
                        INSERT INTO derived_contract_records
                            (tenant_id, contract_family, message_type, record_id,
                             record_version, schema_version, payload)
                        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                        """.strip(),
                        (
                            self._tenant_id,
                            "availability_booking",
                            "booking_command",
                            command["commandId"],
                            1,
                            command["schemaVersion"],
                            json.dumps(
                                command,
                                sort_keys=True,
                                separators=(",", ":"),
                                ensure_ascii=False,
                            ),
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return "new"

    def append_booking_reconciliation(
        self,
        *,
        command: dict[str, Any],
        binding: dict[str, Any],
        prior_result: dict[str, Any],
        reconciliation: dict[str, Any],
    ) -> None:
        records = (command, binding, prior_result, reconciliation)
        self._validate_records(
            records,
            expected_types=(
                "booking_command",
                "calendar_provider_binding",
                "booking_result",
                "booking_reconciliation",
            ),
        )
        validate_booking_command(command)
        validate_booking_result_context(command=command, binding=binding, result=prior_result)
        validate_reconciliation(prior_result, reconciliation)
        self._append(reconciliation, identity_field="reconciliationId")

    def get_booking_result(self, *, command_id: str) -> dict[str, Any] | None:
        if not command_id:
            raise ValueError("command_id is required")
        with self._connection_factory() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('app.tenant_id', %s, true)",
                        (self._tenant_id,),
                    )
                    cursor.execute(
                        """
                        SELECT payload
                        FROM derived_contract_records
                        WHERE tenant_id = %s
                          AND contract_family = 'availability_booking'
                          AND message_type = 'booking_result'
                          AND payload->'commandRef'->>'recordId' = %s
                        ORDER BY record_version DESC
                        LIMIT 1
                        """.strip(),
                        (self._tenant_id, command_id),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        connection.commit()
                        return None
                    result = cast(dict[str, Any], row[0])
                    validate_record(result, "availability_booking", self._registry)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return result

    def _validate_records(
        self,
        records: tuple[dict[str, Any], ...],
        *,
        expected_types: tuple[str, ...],
    ) -> None:
        for record in records:
            validate_record(record, "availability_booking", self._registry)
        if tuple(record["messageType"] for record in records) != expected_types:
            raise ValueError("booking outcome has unexpected message types")
        if any(record["tenantId"] != self._tenant_id for record in records):
            raise TenantIsolationViolation("record tenantId does not match repository tenant")

    def _append(self, record: dict[str, Any], *, identity_field: str) -> None:
        with self._connection_factory() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('app.tenant_id', %s, true)",
                        (self._tenant_id,),
                    )
                    cursor.execute(
                        """
                        INSERT INTO derived_contract_records
                            (tenant_id, contract_family, message_type, record_id,
                             record_version, schema_version, payload)
                        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                        """.strip(),
                        (
                            self._tenant_id,
                            "availability_booking",
                            record["messageType"],
                            record[identity_field],
                            1,
                            record["schemaVersion"],
                            json.dumps(
                                record,
                                sort_keys=True,
                                separators=(",", ":"),
                                ensure_ascii=False,
                            ),
                        ),
                    )
            except Exception:
                connection.rollback()
                raise
            connection.commit()


class QualificationDecisionPairRepository:
    """Atomically append a validated decision pair without activating a production writer."""

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

    def append_decision_pair(
        self,
        *,
        policy: dict[str, Any],
        inputs: dict[str, Any],
        next_question: dict[str, Any],
        readiness: dict[str, Any],
    ) -> None:
        """Validate complete caller-supplied records and append both decisions once."""

        records = (policy, inputs, next_question, readiness)
        for record in records:
            validate_record(record, "qualification_readiness", self._registry)
        expected_types = (
            "qualification_policy",
            "qualification_input_set",
            "next_question_decision",
            "readiness_decision",
        )
        if tuple(record["messageType"] for record in records) != expected_types:
            raise ValueError("qualification decision pair has unexpected message types")
        if any(record["tenantId"] != self._tenant_id for record in records):
            raise TenantIsolationViolation("record tenantId does not match repository tenant")
        validate_qualification_decisions(policy, inputs, next_question, readiness)

        with self._connection_factory() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('app.tenant_id', %s, true)",
                        (self._tenant_id,),
                    )
                    for decision in (next_question, readiness):
                        cursor.execute(
                            """
                            INSERT INTO derived_contract_records
                                (tenant_id, contract_family, message_type, record_id,
                                 record_version, schema_version, payload)
                            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                            """.strip(),
                            (
                                self._tenant_id,
                                "qualification_readiness",
                                decision["messageType"],
                                decision["decisionId"],
                                1,
                                decision["schemaVersion"],
                                json.dumps(
                                    decision,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                    ensure_ascii=False,
                                ),
                            ),
                        )
            except Exception:
                connection.rollback()
                raise
            connection.commit()

    def derive_and_append_decision_pair(
        self,
        *,
        policy: dict[str, Any],
        inputs: dict[str, Any],
        derived_at: datetime,
        expires_at: datetime,
        principal_id: str,
        next_question_id: str,
        readiness_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Derive and atomically append the governed qualification decision pair."""

        next_question, readiness = derive_qualification_decisions(
            policy,
            inputs,
            derived_at=derived_at,
            expires_at=expires_at,
            principal_id=principal_id,
            next_question_id=next_question_id,
            readiness_id=readiness_id,
        )
        self.append_decision_pair(
            policy=policy,
            inputs=inputs,
            next_question=next_question,
            readiness=readiness,
        )
        return next_question, readiness


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
