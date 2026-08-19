"""Artifact metadata, legal holds, fences, and deletion-propagation lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from psycopg.types.json import Jsonb

from .artifacts import ArtifactPointer
from .canonical_repository import Connection, Cursor, VersionConflict
from .evidence_repository import DerivedStore

InvalidationAction = Literal["deleted", "anonymized", "unsupported", "failed"]


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    tenant_id: str
    artifact_id: str
    version: int
    encrypted_object_ref: str | None
    encryption_key_ref: str
    artifact_digest: str
    provenance: dict[str, object]
    classification: str
    retention_class: str
    purpose: str
    captured_at: datetime
    retain_until: datetime | None
    object_lock_until: datetime | None
    provider_legal_hold: bool
    artifact_state: Literal["active", "deleted", "anonymized"]
    tombstone_id: str | None


@dataclass(frozen=True, slots=True)
class PropagationStatus:
    tombstone_id: str
    required_stores: frozenset[str]
    completed_stores: frozenset[str]
    failed_stores: frozenset[str]
    deadline: datetime
    complete: bool
    overdue: bool


class ArtifactRepository:
    """Append-only artifact metadata with query-boundary authorization."""

    def __init__(self, connection: Connection, *, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._connection = connection
        self._tenant_id = tenant_id

    def register(
        self,
        pointer: ArtifactPointer,
        *,
        provenance: dict[str, object],
        classification: str,
        retention_class: str,
        purpose: str,
        captured_at: datetime,
        retain_until: datetime | None,
    ) -> ArtifactRecord:
        if not provenance or not classification or not retention_class or not purpose:
            raise ValueError(
                "artifact provenance, classification, retention, and purpose are required"
            )
        record = ArtifactRecord(
            tenant_id=self._tenant_id,
            artifact_id=pointer.artifact_id,
            version=1,
            encrypted_object_ref=pointer.encrypted_object_ref,
            encryption_key_ref=pointer.encryption_key_ref,
            artifact_digest=pointer.artifact_digest,
            provenance=provenance,
            classification=classification,
            retention_class=retention_class,
            purpose=purpose,
            captured_at=captured_at,
            retain_until=retain_until,
            object_lock_until=pointer.object_lock_until,
            provider_legal_hold=pointer.provider_legal_hold,
            artifact_state="active",
            tombstone_id=None,
        )
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                self._lock_artifact(cursor, pointer.artifact_id)
                cursor.execute(
                    """
                    SELECT version FROM evidence_artifact_versions
                    WHERE tenant_id = %s AND artifact_id = %s
                    ORDER BY version DESC LIMIT 1
                    """.strip(),
                    (self._tenant_id, pointer.artifact_id),
                )
                if cursor.fetchone() is not None:
                    raise VersionConflict("artifact already exists")
                self._insert(cursor, record)
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return record

    def retrieve(
        self,
        artifact_id: str,
        *,
        allowed_purposes: frozenset[str],
        allowed_classifications: frozenset[str],
    ) -> ArtifactRecord | None:
        if not allowed_purposes or not allowed_classifications:
            raise ValueError("purpose and classification grants must both be non-empty")
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    SELECT tenant_id, artifact_id, version, encrypted_object_ref,
                        encryption_key_ref, artifact_digest, provenance, classification,
                        retention_class, purpose, captured_at, retain_until,
                        object_lock_until, provider_legal_hold, artifact_state, tombstone_id
                    FROM evidence_artifact_versions
                    WHERE tenant_id = %s AND artifact_id = %s
                        AND purpose = ANY(%s) AND classification = ANY(%s)
                    ORDER BY version DESC LIMIT 1
                    """.strip(),
                    (
                        self._tenant_id,
                        artifact_id,
                        list(sorted(allowed_purposes)),
                        list(sorted(allowed_classifications)),
                    ),
                )
                row = cursor.fetchone()
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        if row is None:
            return None
        record = self._row_to_record(row)
        return record if record.artifact_state == "active" else None

    def record_legal_hold(
        self,
        *,
        hold_event_id: str,
        hold_id: str,
        artifact_id: str,
        action: Literal["placed", "released"],
        authority_ref: str,
        occurred_at: datetime,
        evidence_event_id: str,
    ) -> None:
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                self._lock_artifact(cursor, artifact_id)
                cursor.execute(
                    """
                    INSERT INTO evidence_legal_hold_events (
                        tenant_id, hold_event_id, hold_id, artifact_id, action,
                        authority_ref, occurred_at, evidence_event_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """.strip(),
                    (
                        self._tenant_id,
                        hold_event_id,
                        hold_id,
                        artifact_id,
                        action,
                        authority_ref,
                        occurred_at,
                        evidence_event_id,
                    ),
                )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()

    def active_legal_holds(self, artifact_id: str) -> frozenset[str]:
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    SELECT DISTINCT ON (hold_id) hold_id, action
                    FROM evidence_legal_hold_events
                    WHERE tenant_id = %s AND artifact_id = %s
                    ORDER BY hold_id, occurred_at DESC, hold_event_id DESC
                    """.strip(),
                    (self._tenant_id, artifact_id),
                )
                rows = cursor.fetchall()
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return frozenset(str(row[0]) for row in rows if row[1] == "placed")

    def mark_deleted(
        self,
        artifact_id: str,
        *,
        expected_version: int,
        tombstone_id: str,
        state: Literal["deleted", "anonymized"],
        now: datetime,
    ) -> ArtifactRecord:
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                self._lock_artifact(cursor, artifact_id)
                cursor.execute(
                    """
                    SELECT tenant_id, artifact_id, version, encrypted_object_ref,
                        encryption_key_ref, artifact_digest, provenance, classification,
                        retention_class, purpose, captured_at, retain_until,
                        object_lock_until, provider_legal_hold, artifact_state, tombstone_id
                    FROM evidence_artifact_versions
                    WHERE tenant_id = %s AND artifact_id = %s
                    ORDER BY version DESC LIMIT 1 FOR UPDATE
                    """.strip(),
                    (self._tenant_id, artifact_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise KeyError(f"unknown artifact: {artifact_id}")
                current = self._row_to_record(row)
                if current.version != expected_version or current.artifact_state != "active":
                    raise VersionConflict("artifact version/state changed before deletion")
                if current.provider_legal_hold:
                    raise PermissionError("provider legal hold prohibits artifact deletion")
                if current.object_lock_until is not None and now < current.object_lock_until:
                    raise PermissionError("provider object lock prohibits artifact deletion")
                cursor.execute(
                    """
                    SELECT 1 FROM (
                        SELECT DISTINCT ON (hold_id) action
                        FROM evidence_legal_hold_events
                        WHERE tenant_id = %s AND artifact_id = %s
                        ORDER BY hold_id, occurred_at DESC, hold_event_id DESC
                    ) holds WHERE action = 'placed' LIMIT 1
                    """.strip(),
                    (self._tenant_id, artifact_id),
                )
                if cursor.fetchone() is not None:
                    raise PermissionError("active legal hold prohibits artifact deletion")
                deleted = ArtifactRecord(
                    tenant_id=current.tenant_id,
                    artifact_id=current.artifact_id,
                    version=current.version + 1,
                    encrypted_object_ref=None,
                    encryption_key_ref=current.encryption_key_ref,
                    artifact_digest=current.artifact_digest,
                    provenance=current.provenance,
                    classification=current.classification,
                    retention_class=current.retention_class,
                    purpose=current.purpose,
                    captured_at=current.captured_at,
                    retain_until=current.retain_until,
                    object_lock_until=current.object_lock_until,
                    provider_legal_hold=current.provider_legal_hold,
                    artifact_state=state,
                    tombstone_id=tombstone_id,
                )
                self._insert(cursor, deleted)
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return deleted

    def is_fenced(self, target_ref: str) -> bool:
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    "SELECT 1 FROM projection_fences WHERE tenant_id = %s AND target_ref = %s LIMIT 1",
                    (self._tenant_id, target_ref),
                )
                fenced = cursor.fetchone() is not None
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return fenced

    def _insert(self, cursor: Cursor, record: ArtifactRecord) -> None:
        cursor.execute(
            """
            INSERT INTO evidence_artifact_versions (
                tenant_id, artifact_id, version, encrypted_object_ref, encryption_key_ref,
                artifact_digest, provenance, classification, retention_class, purpose,
                captured_at, retain_until, artifact_state, tombstone_id,
                object_lock_until, provider_legal_hold
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """.strip(),
            (
                record.tenant_id,
                record.artifact_id,
                record.version,
                record.encrypted_object_ref,
                record.encryption_key_ref,
                record.artifact_digest,
                Jsonb(record.provenance),
                record.classification,
                record.retention_class,
                record.purpose,
                record.captured_at,
                record.retain_until,
                record.artifact_state,
                record.tombstone_id,
                record.object_lock_until,
                record.provider_legal_hold,
            ),
        )

    def _lock_artifact(self, cursor: Cursor, artifact_id: str) -> None:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"artifact:{self._tenant_id}:{artifact_id}",),
        )

    @staticmethod
    def _row_to_record(row: tuple[object, ...]) -> ArtifactRecord:
        return ArtifactRecord(
            tenant_id=str(row[0]),
            artifact_id=str(row[1]),
            version=int(cast(int, row[2])),
            encrypted_object_ref=None if row[3] is None else str(row[3]),
            encryption_key_ref=str(row[4]),
            artifact_digest=str(row[5]),
            provenance=cast(dict[str, object], row[6]),
            classification=str(row[7]),
            retention_class=str(row[8]),
            purpose=str(row[9]),
            captured_at=cast(datetime, row[10]),
            retain_until=cast(datetime | None, row[11]),
            object_lock_until=cast(datetime | None, row[12]),
            provider_legal_hold=bool(row[13]),
            artifact_state=cast(Literal["active", "deleted", "anonymized"], row[14]),
            tombstone_id=None if row[15] is None else str(row[15]),
        )

    def _set_tenant(self, cursor: Cursor) -> None:
        cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))


class DeletionPropagationRepository:
    def __init__(self, connection: Connection, *, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._connection = connection
        self._tenant_id = tenant_id

    def acknowledge(
        self,
        *,
        invalidation_event_id: str,
        tombstone_id: str,
        derived_store: DerivedStore,
        action: InvalidationAction,
        occurred_at: datetime,
        worker_ref: str,
    ) -> int:
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"invalidation:{self._tenant_id}:{tombstone_id}:{derived_store}",),
                )
                cursor.execute(
                    """
                    SELECT store_sequence FROM derived_invalidation_events
                    WHERE tenant_id = %s AND tombstone_id = %s AND derived_store = %s
                    ORDER BY store_sequence DESC LIMIT 1
                    """.strip(),
                    (self._tenant_id, tombstone_id, derived_store),
                )
                row = cursor.fetchone()
                if row is None:
                    raise KeyError("no invalidation request exists for derived store")
                sequence = int(cast(int, row[0])) + 1
                cursor.execute(
                    """
                    INSERT INTO derived_invalidation_events (
                        tenant_id, invalidation_event_id, tombstone_id, derived_store,
                        store_sequence, action, occurred_at, worker_ref
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """.strip(),
                    (
                        self._tenant_id,
                        invalidation_event_id,
                        tombstone_id,
                        derived_store,
                        sequence,
                        action,
                        occurred_at,
                        worker_ref,
                    ),
                )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return sequence

    def status(
        self,
        tombstone_id: str,
        *,
        completion_slo: timedelta,
        now: datetime,
    ) -> PropagationStatus:
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    SELECT deleted_at FROM evidence_deletion_tombstones
                    WHERE tenant_id = %s AND tombstone_id = %s
                    """.strip(),
                    (self._tenant_id, tombstone_id),
                )
                tombstone = cursor.fetchone()
                if tombstone is None:
                    raise KeyError(f"unknown tombstone: {tombstone_id}")
                cursor.execute(
                    """
                    SELECT DISTINCT ON (derived_store) derived_store, action
                    FROM derived_invalidation_events
                    WHERE tenant_id = %s AND tombstone_id = %s
                    ORDER BY derived_store, store_sequence DESC
                    """.strip(),
                    (self._tenant_id, tombstone_id),
                )
                rows = cursor.fetchall()
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        latest = {str(row[0]): str(row[1]) for row in rows}
        successful = {"deleted", "anonymized", "unsupported"}
        required = frozenset(latest)
        completed = frozenset(store for store, action in latest.items() if action in successful)
        failed = frozenset(store for store, action in latest.items() if action == "failed")
        deadline = cast(datetime, tombstone[0]).astimezone(UTC) + completion_slo
        complete = completed == required
        return PropagationStatus(
            tombstone_id=tombstone_id,
            required_stores=required,
            completed_stores=completed,
            failed_stores=failed,
            deadline=deadline,
            complete=complete,
            overdue=not complete and now.astimezone(UTC) > deadline,
        )

    def _set_tenant(self, cursor: Cursor) -> None:
        cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
