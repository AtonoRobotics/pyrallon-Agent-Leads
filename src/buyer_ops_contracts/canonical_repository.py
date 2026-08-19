"""Tenant-scoped canonical persistence for ontology records."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol, cast

from .canonical_admission import (
    FactVerifier,
    validate_agreement_qualification,
    validate_reference_graph,
    validate_representation_relationship,
    validate_update,
    validate_verified_fact_admission,
)
from .errors import ContractViolation, Violation
from .semantic import validate_semantics
from .structural import validate_record


class Cursor(Protocol):
    def __enter__(self) -> Cursor: ...

    def __exit__(self, *args: object) -> None: ...

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None: ...

    def fetchone(self) -> tuple[object, ...] | None: ...

    def fetchall(self) -> list[tuple[object, ...]]: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class TenantIsolationViolation(ValueError):
    """Raised when a record does not belong to the repository tenant."""


class VersionConflict(RuntimeError):
    """Raised when a canonical record update is not based on its current version."""


class CanonicalRepository:
    """Persist contract-valid ontology records in a required tenant scope."""

    def __init__(
        self,
        connection: Connection,
        *,
        tenant_id: str,
        fact_verifier: FactVerifier | None = None,
    ) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._connection = connection
        self._tenant_id = tenant_id
        self._fact_verifier = fact_verifier

    def get(self, record_id: str) -> dict[str, Any] | None:
        """Read one current record; tenant scope is always part of the query."""
        if not record_id:
            raise ValueError("record_id is required")
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    SELECT record
                    FROM canonical_records_current
                    WHERE tenant_id = %s AND record_id = %s
                    """.strip(),
                    (self._tenant_id, record_id),
                )
                row = cursor.fetchone()
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return None if row is None else cast(dict[str, Any], row[0])

    def history(self, record_id: str) -> list[dict[str, Any]]:
        """Reconstruct every admitted version in deterministic version order."""
        if not record_id:
            raise ValueError("record_id is required")
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    SELECT record
                    FROM canonical_record_versions
                    WHERE tenant_id = %s AND record_id = %s
                    ORDER BY version
                    """.strip(),
                    (self._tenant_id, record_id),
                )
                rows = cursor.fetchall()
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return [cast(dict[str, Any], row[0]) for row in rows]

    def list_by_type(self, record_type: str) -> list[dict[str, Any]]:
        """List current records of one ontology type in this tenant."""
        if not record_type:
            raise ValueError("record_type is required")
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    SELECT record
                    FROM canonical_records_current
                    WHERE tenant_id = %s AND record_type = %s
                    ORDER BY record_id
                    """.strip(),
                    (self._tenant_id, record_type),
                )
                rows = cursor.fetchall()
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return [cast(dict[str, Any], row[0]) for row in rows]

    def save(
        self,
        raw_record: Mapping[str, Any],
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """Validate and atomically append a new canonical record version."""
        record = dict(raw_record)
        validate_record(record, "ontology")
        validate_semantics(record)
        if record["tenantId"] != self._tenant_id:
            raise TenantIsolationViolation("record tenantId does not match repository tenant")
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                record = self._save_on(cursor, record, expected_version=expected_version)
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return record

    def _save_on(
        self,
        cursor: Cursor,
        raw_record: Mapping[str, Any],
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        record = dict(raw_record)
        validate_record(record, "ontology")
        validate_semantics(record)
        if record["tenantId"] != self._tenant_id:
            raise TenantIsolationViolation("record tenantId does not match repository tenant")
        record_id = str(record["id"])
        version = int(record["version"])
        cursor.execute(
            """
            SELECT version, record
            FROM canonical_records_current
            WHERE tenant_id = %s AND record_id = %s
            FOR UPDATE
            """.strip(),
            (self._tenant_id, record_id),
        )
        current = cursor.fetchone()
        current_version = None if current is None else int(cast(int, current[0]))
        if current_version is None:
            if expected_version is not None or version != 1:
                raise VersionConflict("new canonical records must start at version 1")
        elif expected_version != current_version or version != current_version + 1:
            raise VersionConflict(
                f"expected version {expected_version} does not match current version "
                f"{current_version}"
            )
        else:
            assert current is not None
            validate_update(cast(dict[str, Any], current[1]), record)
        self._validate_cross_record(cursor, record)
        self._write_record(cursor, record)
        self._sync_actor_tenancy(cursor, record)
        return record

    def supersede(
        self,
        raw_prior_update: Mapping[str, Any],
        raw_successor: Mapping[str, Any],
        *,
        expected_prior_version: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Close one current interval and activate its explicit successor atomically."""
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                result = self.supersede_on(
                    cursor,
                    raw_prior_update,
                    raw_successor,
                    expected_prior_version=expected_prior_version,
                )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return result

    def supersede_on(
        self,
        cursor: Cursor,
        raw_prior_update: Mapping[str, Any],
        raw_successor: Mapping[str, Any],
        *,
        expected_prior_version: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Supersede records inside a caller-owned database transaction."""
        prior_update = dict(raw_prior_update)
        successor = dict(raw_successor)
        for record in (prior_update, successor):
            validate_record(record, "ontology")
            validate_semantics(record)
            if record["tenantId"] != self._tenant_id:
                raise TenantIsolationViolation("record tenantId does not match repository tenant")
        if successor.get("supersedesId") != prior_update["id"]:
            raise ValueError("successor supersedesId must identify the prior record")
        if successor["recordType"] != prior_update["recordType"]:
            raise ValueError("successor and prior record types must match")
        if successor["version"] != 1:
            raise VersionConflict("a successor is a new record and must start at version 1")
        if prior_update.get("status") != "superseded" or "effectiveTo" not in prior_update:
            raise ValueError("prior record must be explicitly closed as superseded")
        if prior_update["effectiveTo"] != successor["effectiveFrom"]:
            raise ValueError("prior effectiveTo must equal successor effectiveFrom")
        if not prior_update["sourceEvidenceIds"] or not successor["sourceEvidenceIds"]:
            raise ValueError("supersession requires transition evidence on both records")
        epistemic_state_fields = {
            "Evidence": "evidenceState",
            "Assertion": "assertionState",
            "VerifiedFact": "factState",
            "Inference": "inferenceState",
            "Memory": "memoryState",
        }
        if prior_update["recordType"] in epistemic_state_fields:
            state_field = epistemic_state_fields[prior_update["recordType"]]
            if prior_update[state_field] != "superseded":
                raise ValueError("corrected epistemic item must enter superseded validity")
            if successor[state_field] != "current":
                raise ValueError("replacement epistemic item must start current")

        cursor.execute(
            """
            SELECT version, record FROM canonical_records_current
            WHERE tenant_id = %s AND record_id = %s FOR UPDATE
            """.strip(),
            (self._tenant_id, prior_update["id"]),
        )
        current = cursor.fetchone()
        if current is None:
            raise KeyError(f"unknown prior record: {prior_update['id']}")
        current_version = int(cast(int, current[0]))
        if (
            current_version != expected_prior_version
            or prior_update["version"] != current_version + 1
        ):
            raise VersionConflict("prior record changed before supersession")
        validate_update(cast(dict[str, Any], current[1]), prior_update)
        cursor.execute(
            """
            SELECT 1 FROM canonical_records_current
            WHERE tenant_id = %s AND record_id = %s FOR UPDATE
            """.strip(),
            (self._tenant_id, successor["id"]),
        )
        if cursor.fetchone() is not None:
            raise VersionConflict("successor id already exists")
        self._validate_cross_record(cursor, prior_update)
        self._validate_cross_record(cursor, successor)
        self._write_record(cursor, prior_update)
        self._write_record(cursor, successor)
        return prior_update, successor

    def apply_correction(
        self,
        raw_correction: Mapping[str, Any],
        raw_corrected_update: Mapping[str, Any],
        *,
        expected_corrected_version: int,
        raw_replacement: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
        """Atomically apply an epistemic invalidation or superseding replacement."""
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                result = self.apply_correction_on(
                    cursor,
                    raw_correction,
                    raw_corrected_update,
                    expected_corrected_version=expected_corrected_version,
                    raw_replacement=raw_replacement,
                )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return result

    def apply_correction_on(
        self,
        cursor: Cursor,
        raw_correction: Mapping[str, Any],
        raw_corrected_update: Mapping[str, Any],
        *,
        expected_corrected_version: int,
        raw_replacement: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
        """Apply a correction inside a caller-owned database transaction."""
        correction = dict(raw_correction)
        corrected_update = dict(raw_corrected_update)
        replacement = None if raw_replacement is None else dict(raw_replacement)
        records = [correction, corrected_update] + ([] if replacement is None else [replacement])
        for record in records:
            validate_record(record, "ontology")
            validate_semantics(record)
            if record["tenantId"] != self._tenant_id:
                raise TenantIsolationViolation("record tenantId does not match repository tenant")
        if correction["recordType"] != "Correction" or correction["correctionState"] != "applied":
            raise ValueError("correction must be an applied Correction record")
        if correction["correctedItemId"] != corrected_update["id"]:
            raise ValueError("Correction.correctedItemId must match the corrected update")
        state_fields = {
            "Evidence": "evidenceState",
            "Assertion": "assertionState",
            "VerifiedFact": "factState",
            "Inference": "inferenceState",
            "Memory": "memoryState",
        }
        state_field = state_fields.get(corrected_update["recordType"])
        if state_field is None:
            raise ValueError("only concrete epistemic records can be corrected")
        action = correction["correctionAction"]
        if action == "replace":
            if replacement is None or correction.get("replacementItemId") != replacement["id"]:
                raise ValueError("replace requires the exact declared replacement")
            if replacement["recordType"] != corrected_update["recordType"]:
                raise ValueError("replacement must preserve the concrete epistemic type")
            if replacement.get("supersedesId") != corrected_update["id"]:
                raise ValueError("replacement must explicitly supersede the corrected item")
            if corrected_update.get("effectiveTo") != replacement["effectiveFrom"]:
                raise ValueError("corrected effectiveTo must equal replacement effectiveFrom")
            if (
                corrected_update[state_field] != "superseded"
                or corrected_update["status"] != "superseded"
            ):
                raise ValueError("replaced item must be closed as superseded")
            if (
                replacement[state_field] != "current"
                or replacement["status"] != "active"
                or replacement["version"] != 1
            ):
                raise ValueError("replacement must begin as a new current record")
        elif (
            replacement is not None
            or corrected_update[state_field] != "invalid"
            or corrected_update["status"] != "invalid"
        ):
            raise ValueError("invalidate requires no replacement and an invalid corrected item")

        pending = {corrected_update["id"]: corrected_update, correction["id"]: correction}
        if replacement is not None:
            pending[replacement["id"]] = replacement
        cursor.execute(
            "SELECT version, record FROM canonical_records_current WHERE tenant_id = %s AND record_id = %s FOR UPDATE",
            (self._tenant_id, corrected_update["id"]),
        )
        row = cursor.fetchone()
        if row is None:
            raise KeyError(f"unknown corrected item: {corrected_update['id']}")
        previous = cast(dict[str, Any], row[1])
        if int(cast(int, row[0])) != expected_corrected_version:
            raise VersionConflict("corrected item changed before correction")
        validate_update(previous, corrected_update)
        for new_record in ([replacement] if replacement is not None else []) + [correction]:
            assert new_record is not None
            cursor.execute(
                "SELECT 1 FROM canonical_records_current WHERE tenant_id = %s AND record_id = %s FOR UPDATE",
                (self._tenant_id, new_record["id"]),
            )
            if cursor.fetchone() is not None:
                raise VersionConflict("correction or replacement id already exists")
        for record in records:
            self._validate_cross_record(cursor, record, pending=pending)
        self._write_record(cursor, corrected_update)
        if replacement is not None:
            self._write_record(cursor, replacement)
        self._write_record(cursor, correction)
        return corrected_update, replacement, correction

    def load_current_on(
        self, cursor: Cursor, record_id: str, *, for_update: bool = False
    ) -> dict[str, Any] | None:
        """Load a tenant-scoped current record inside a caller-owned transaction."""
        lock = "FOR UPDATE" if for_update else "FOR SHARE"
        cursor.execute(
            f"SELECT record FROM canonical_records_current WHERE tenant_id = %s AND record_id = %s {lock}",
            (self._tenant_id, record_id),
        )
        row = cursor.fetchone()
        return None if row is None else cast(dict[str, Any], row[0])

    def save_on(
        self,
        cursor: Cursor,
        raw_record: Mapping[str, Any],
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """Save a canonical record inside a caller-owned database transaction."""
        return self._save_on(cursor, raw_record, expected_version=expected_version)

    def _write_record(self, cursor: Cursor, record: dict[str, Any]) -> None:
        payload = json.dumps(record, separators=(",", ":"))
        parameters = (
            self._tenant_id,
            record["id"],
            record["version"],
            record["recordType"],
            record["schemaVersion"],
            payload,
        )
        cursor.execute(
            """
            INSERT INTO canonical_record_versions
                (tenant_id, record_id, version, record_type, schema_version, record)
            VALUES (%s, %s, %s, %s, %s, %s)
            """.strip(),
            parameters,
        )
        cursor.execute(
            """
            INSERT INTO canonical_records_current
                (tenant_id, record_id, version, record_type, schema_version, record)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, record_id) DO UPDATE SET
                version = EXCLUDED.version,
                record_type = EXCLUDED.record_type,
                schema_version = EXCLUDED.schema_version,
                record = EXCLUDED.record,
                updated_at = clock_timestamp()
            """.strip(),
            parameters,
        )

    def _validate_cross_record(
        self,
        cursor: Cursor,
        record: dict[str, Any],
        *,
        pending: Mapping[str, dict[str, Any]] | None = None,
    ) -> None:
        def resolve(record_id: str) -> dict[str, Any] | None:
            if pending is not None and record_id in pending:
                return pending[record_id]
            return self._load_current(cursor, record_id)

        validate_reference_graph(record, resolve)
        if record["recordType"] == "VerifiedFact":
            evidence = [
                cast(dict[str, Any], self._load_current(cursor, str(record_id)))
                for record_id in record["supportingEvidenceIds"]
            ]
            validate_verified_fact_admission(record, evidence, self._fact_verifier)
        if record["recordType"] == "RepresentationRelationship":
            agreement = self._load_current(cursor, str(record["agreementId"]))
            validate_representation_relationship(record, agreement)
            if record["relationshipState"] == "active":
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"active-representation:{self._tenant_id}:{record['buyingPartyId']}",),
                )
                cursor.execute(
                    """
                    SELECT record_id FROM canonical_records_current
                    WHERE tenant_id = %s
                      AND record_type = 'RepresentationRelationship'
                      AND record_id <> %s
                      AND record->>'buyingPartyId' = %s
                      AND record->>'relationshipState' = 'active'
                    FOR SHARE
                    """.strip(),
                    (self._tenant_id, record["id"], record["buyingPartyId"]),
                )
                if cursor.fetchone() is not None:
                    raise ContractViolation(
                        [
                            Violation(
                                "ACTIVE_REPRESENTATION_CARDINALITY",
                                "$.buyingPartyId",
                                "another active representation relationship already exists",
                            )
                        ]
                    )
        if (
            record["recordType"] == "AgreementQualification"
            and record["result"] == "qualified"
            and "agreementId" in record
        ):
            agreement = self._load_current(cursor, str(record["agreementId"]))
            validate_agreement_qualification(record, agreement)

    def _load_current(self, cursor: Cursor, record_id: str) -> dict[str, Any] | None:
        cursor.execute(
            """
            SELECT record FROM canonical_records_current
            WHERE tenant_id = %s AND record_id = %s FOR SHARE
            """.strip(),
            (self._tenant_id, record_id),
        )
        row = cursor.fetchone()
        return None if row is None else cast(dict[str, Any], row[0])

    def _sync_actor_tenancy(self, cursor: Cursor, record: dict[str, Any]) -> None:
        if record.get("recordType") != "Authorization":
            return
        actor_id = str(record["granteeId"])
        if record.get("authorizationState") == "active" and record.get("status") == "active":
            cursor.execute(
                """
                INSERT INTO operator_actor_tenancies (actor_id, tenant_id, authorization_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (actor_id, tenant_id, authorization_id) DO NOTHING
                """.strip(),
                (actor_id, self._tenant_id, record["id"]),
            )
            return
        cursor.execute(
            """
            DELETE FROM operator_actor_tenancies
            WHERE actor_id = %s AND tenant_id = %s AND authorization_id = %s
            """.strip(),
            (actor_id, self._tenant_id, record["id"]),
        )

    def _set_tenant(self, cursor: Cursor) -> None:
        cursor.execute(
            "SELECT set_config('app.tenant_id', %s, true)",
            (self._tenant_id,),
        )
