"""Atomic PostgreSQL repository for the PKT-02 evidence boundary."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg.types.json import Jsonb

from .audit import TenantEvidenceExport, build_tenant_export
from .canonical_repository import Connection
from .evidence import (
    GENESIS_HASH,
    EvidenceCheckpoint,
    EvidenceEntry,
    EvidenceEventType,
    build_entry,
    sign_checkpoint,
    verify_chain,
)
from .retention import RetentionPolicy

DerivedStore = Literal[
    "object_index",
    "pgvector",
    "neo4j",
    "summary",
    "memory",
    "cache",
    "evaluation_corpus",
]
FenceTargetKind = Literal["subject", "evidence", "descendants"]


class DeletionDenied(RuntimeError):
    """Raised when retention or an active legal hold prohibits deletion."""


@dataclass(frozen=True, slots=True)
class DeletionReceipt:
    evidence_entry: EvidenceEntry
    fence_id: str
    fence_sequence: int
    tombstone_id: str
    invalidation_event_ids: tuple[str, ...]


class EvidenceRepository:
    """Append and retrieve metadata-only evidence in a mandatory tenant scope."""

    def __init__(self, connection: Connection, *, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._connection = connection
        self._tenant_id = tenant_id

    def append(
        self,
        *,
        event_id: str,
        event_type: EvidenceEventType,
        occurred_at: str,
        captured_at: str,
        classification: str,
        retention_class: str,
        purpose: str,
        payload_digest: str,
        provenance_refs: tuple[str, ...],
        artifact_ids: tuple[str, ...] = (),
        canonical_record_ids: tuple[str, ...] = (),
        workflow_id: str | None = None,
        effect_attempt_id: str | None = None,
    ) -> EvidenceEntry:
        """Serialize one tenant chain, compute its next link, and append atomically."""
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"evidence-ledger:{self._tenant_id}",),
                )
                cursor.execute(
                    """
                    SELECT sequence, entry_hash
                    FROM evidence_ledger
                    WHERE tenant_id = %s
                    ORDER BY sequence DESC
                    LIMIT 1
                    """.strip(),
                    (self._tenant_id,),
                )
                previous = cursor.fetchone()
                sequence = 1 if previous is None else int(cast(int, previous[0])) + 1
                prior_hash = GENESIS_HASH if previous is None else str(previous[1])
                entry = build_entry(
                    tenant_id=self._tenant_id,
                    sequence=sequence,
                    event_id=event_id,
                    event_type=event_type,
                    occurred_at=occurred_at,
                    captured_at=captured_at,
                    classification=classification,
                    retention_class=retention_class,
                    purpose=purpose,
                    payload_digest=payload_digest,
                    provenance_refs=provenance_refs,
                    artifact_ids=artifact_ids,
                    canonical_record_ids=canonical_record_ids,
                    workflow_id=workflow_id,
                    effect_attempt_id=effect_attempt_id,
                    prior_hash=prior_hash,
                )
                cursor.execute(
                    """
                    INSERT INTO evidence_ledger (
                        tenant_id, sequence, event_id, event_type, occurred_at, captured_at,
                        classification, retention_class, purpose, payload_digest,
                        provenance_refs, artifact_ids, canonical_record_ids, workflow_id,
                        effect_attempt_id, prior_hash, entry_hash
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    """.strip(),
                    self._entry_parameters(entry),
                )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return entry

    def reconstruct(self) -> list[EvidenceEntry]:
        """Return and cryptographically verify the complete tenant audit chain."""
        entries = self._select_entries()
        verify_chain(entries)
        return entries

    def retrieve(
        self,
        *,
        allowed_purposes: frozenset[str],
        allowed_classifications: frozenset[str],
    ) -> list[EvidenceEntry]:
        """Return metadata only after applying purpose and classification at the query boundary."""
        if not allowed_purposes or not allowed_classifications:
            raise ValueError("purpose and classification grants must both be non-empty")
        return self._select_entries(
            purposes=tuple(sorted(allowed_purposes)),
            classifications=tuple(sorted(allowed_classifications)),
        )

    def create_checkpoint(
        self,
        *,
        through_sequence: int,
        signer_key_id: str,
        private_key: Ed25519PrivateKey,
        signed_at: datetime | None = None,
    ) -> EvidenceCheckpoint:
        """Sign and persist a checkpoint bound to an existing ledger head."""
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    SELECT entry_hash
                    FROM evidence_ledger
                    WHERE tenant_id = %s AND sequence = %s
                    """.strip(),
                    (self._tenant_id, through_sequence),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError("checkpoint sequence does not exist")
                checkpoint = sign_checkpoint(
                    tenant_id=self._tenant_id,
                    through_sequence=through_sequence,
                    head_hash=str(row[0]),
                    signer_key_id=signer_key_id,
                    private_key=private_key,
                    signed_at=signed_at,
                )
                cursor.execute(
                    """
                    INSERT INTO evidence_checkpoints (
                        tenant_id, through_sequence, head_hash, signed_at,
                        signer_key_id, signature
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """.strip(),
                    (
                        checkpoint.tenant_id,
                        checkpoint.through_sequence,
                        checkpoint.head_hash,
                        checkpoint.signed_at,
                        checkpoint.signer_key_id,
                        checkpoint.signature,
                    ),
                )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return checkpoint

    def request_deletion(
        self,
        *,
        event_id: str,
        tombstone_id: str,
        fence_id: str,
        target_ref: str,
        target_kind: FenceTargetKind,
        deleted_record_class: str,
        reason_code: str,
        occurred_at: str,
        captured_at: str,
        classification: str,
        retention_class: str,
        purpose: str,
        payload_digest: str,
        provenance_refs: tuple[str, ...],
        enabled_derived_stores: frozenset[DerivedStore],
        retention_policy: RetentionPolicy,
        retain_until: datetime,
        active_legal_hold_ids: frozenset[str],
        now: datetime,
    ) -> DeletionReceipt:
        """Atomically append deletion evidence, its retrieval fence, and purge requests."""
        if not retention_policy.deletion_eligible(
            retain_until=retain_until,
            active_legal_hold_ids=active_legal_hold_ids,
            now=now,
        ):
            raise DeletionDenied("retention period or active legal hold prohibits deletion")
        if not tombstone_id or not fence_id or not target_ref or not reason_code:
            raise ValueError("deletion tombstone, fence, target, and reason are required")

        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"evidence-ledger:{self._tenant_id}",),
                )
                cursor.execute(
                    """
                    SELECT sequence, entry_hash FROM evidence_ledger
                    WHERE tenant_id = %s ORDER BY sequence DESC LIMIT 1
                    """.strip(),
                    (self._tenant_id,),
                )
                previous = cursor.fetchone()
                sequence = 1 if previous is None else int(cast(int, previous[0])) + 1
                prior_hash = GENESIS_HASH if previous is None else str(previous[1])
                entry = build_entry(
                    tenant_id=self._tenant_id,
                    sequence=sequence,
                    event_id=event_id,
                    event_type="deletion",
                    occurred_at=occurred_at,
                    captured_at=captured_at,
                    classification=classification,
                    retention_class=retention_class,
                    purpose=purpose,
                    payload_digest=payload_digest,
                    provenance_refs=provenance_refs,
                    prior_hash=prior_hash,
                )
                cursor.execute(
                    """
                    INSERT INTO evidence_ledger (
                        tenant_id, sequence, event_id, event_type, occurred_at, captured_at,
                        classification, retention_class, purpose, payload_digest,
                        provenance_refs, artifact_ids, canonical_record_ids, workflow_id,
                        effect_attempt_id, prior_hash, entry_hash
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    """.strip(),
                    self._entry_parameters(entry),
                )
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"projection-fence:{self._tenant_id}",),
                )
                cursor.execute(
                    """
                    SELECT fence_sequence FROM projection_fences
                    WHERE tenant_id = %s ORDER BY fence_sequence DESC LIMIT 1
                    """.strip(),
                    (self._tenant_id,),
                )
                previous_fence = cursor.fetchone()
                fence_sequence = (
                    1 if previous_fence is None else int(cast(int, previous_fence[0])) + 1
                )
                cursor.execute(
                    """
                    INSERT INTO projection_fences (
                        tenant_id, fence_sequence, fence_id, target_ref, target_kind,
                        cause_event_id, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """.strip(),
                    (
                        self._tenant_id,
                        fence_sequence,
                        fence_id,
                        target_ref,
                        target_kind,
                        event_id,
                        occurred_at,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO evidence_deletion_tombstones (
                        tenant_id, tombstone_id, deletion_event_id, deleted_record_class,
                        deleted_at, reason_code, projection_fence_sequence
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """.strip(),
                    (
                        self._tenant_id,
                        tombstone_id,
                        event_id,
                        deleted_record_class,
                        occurred_at,
                        reason_code,
                        fence_sequence,
                    ),
                )
                invalidation_ids: list[str] = []
                for store in sorted(enabled_derived_stores):
                    invalidation_id = f"{tombstone_id}:{store}:requested"
                    invalidation_ids.append(invalidation_id)
                    cursor.execute(
                        """
                        INSERT INTO derived_invalidation_events (
                            tenant_id, invalidation_event_id, tombstone_id,
                            derived_store, store_sequence, action, occurred_at
                        ) VALUES (%s, %s, %s, %s, 1, 'requested', %s)
                        """.strip(),
                        (
                            self._tenant_id,
                            invalidation_id,
                            tombstone_id,
                            store,
                            occurred_at,
                        ),
                    )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return DeletionReceipt(
            evidence_entry=entry,
            fence_id=fence_id,
            fence_sequence=fence_sequence,
            tombstone_id=tombstone_id,
            invalidation_event_ids=tuple(invalidation_ids),
        )

    def export_tenant_evidence(self, *, generated_at: datetime) -> TenantEvidenceExport:
        """Export reconstructable metadata and signatures, never source bodies or reasoning."""
        entries = tuple(self.reconstruct())
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    SELECT tenant_id, through_sequence, head_hash, signed_at,
                        signer_key_id, signature
                    FROM evidence_checkpoints WHERE tenant_id = %s
                    ORDER BY through_sequence, signer_key_id
                    """.strip(),
                    (self._tenant_id,),
                )
                checkpoints = tuple(
                    EvidenceCheckpoint(
                        tenant_id=str(row[0]),
                        through_sequence=int(cast(int, row[1])),
                        head_hash=str(row[2]),
                        signed_at=(
                            row[3].isoformat() if isinstance(row[3], datetime) else str(row[3])
                        ),
                        signer_key_id=str(row[4]),
                        signature=str(row[5]),
                    )
                    for row in cursor.fetchall()
                )
                artifact_versions = self._export_rows(
                    cursor, "evidence_artifact_versions", "artifact_id, version"
                )
                legal_holds = self._export_rows(
                    cursor, "evidence_legal_hold_events", "occurred_at, hold_event_id"
                )
                fences = self._export_rows(cursor, "projection_fences", "fence_sequence")
                tombstones = self._export_rows(
                    cursor, "evidence_deletion_tombstones", "deleted_at, tombstone_id"
                )
                invalidations = self._export_rows(
                    cursor,
                    "derived_invalidation_events",
                    "tombstone_id, derived_store, store_sequence",
                )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return build_tenant_export(
            tenant_id=self._tenant_id,
            generated_at=generated_at.isoformat(),
            entries=entries,
            checkpoints=checkpoints,
            artifact_versions=artifact_versions,
            legal_hold_events=legal_holds,
            projection_fences=fences,
            deletion_tombstones=tombstones,
            invalidation_events=invalidations,
        )

    def _export_rows(
        self, cursor: Any, table_name: str, order_by: str
    ) -> tuple[dict[str, Any], ...]:
        allowed = {
            "evidence_artifact_versions",
            "evidence_legal_hold_events",
            "projection_fences",
            "evidence_deletion_tombstones",
            "derived_invalidation_events",
        }
        if table_name not in allowed:
            raise ValueError("unsupported evidence export table")
        cursor.execute(
            f"SELECT to_jsonb(exported) FROM {table_name} AS exported "
            f"WHERE tenant_id = %s ORDER BY {order_by}",
            (self._tenant_id,),
        )
        return tuple(cast(dict[str, Any], row[0]) for row in cursor.fetchall())

    def _select_entries(
        self,
        *,
        purposes: tuple[str, ...] | None = None,
        classifications: tuple[str, ...] | None = None,
    ) -> list[EvidenceEntry]:
        filters = ""
        parameters: tuple[object, ...] = (self._tenant_id,)
        if purposes is not None and classifications is not None:
            filters = " AND purpose = ANY(%s) AND classification = ANY(%s)"
            parameters = (self._tenant_id, list(purposes), list(classifications))
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    (
                        "SELECT tenant_id, sequence, event_id, event_type, occurred_at, "
                        "captured_at, classification, retention_class, purpose, payload_digest, "
                        "provenance_refs, artifact_ids, canonical_record_ids, workflow_id, "
                        "effect_attempt_id, prior_hash, entry_hash FROM evidence_ledger "
                        f"WHERE tenant_id = %s{filters} ORDER BY sequence"
                    ),
                    parameters,
                )
                rows = cursor.fetchall()
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return [self._row_to_entry(row) for row in rows]

    @staticmethod
    def _entry_parameters(entry: EvidenceEntry) -> tuple[object, ...]:
        return (
            entry.tenant_id,
            entry.sequence,
            entry.event_id,
            entry.event_type,
            entry.occurred_at,
            entry.captured_at,
            entry.classification,
            entry.retention_class,
            entry.purpose,
            entry.payload_digest,
            Jsonb(entry.provenance_refs),
            Jsonb(entry.artifact_ids),
            Jsonb(entry.canonical_record_ids),
            entry.workflow_id,
            entry.effect_attempt_id,
            entry.prior_hash,
            entry.entry_hash,
        )

    @staticmethod
    def _row_to_entry(row: Sequence[object]) -> EvidenceEntry:
        def timestamp(value: object) -> str:
            return value.isoformat() if isinstance(value, datetime) else str(value)

        return EvidenceEntry(
            tenant_id=str(row[0]),
            sequence=int(cast(int, row[1])),
            event_id=str(row[2]),
            event_type=cast(EvidenceEventType, row[3]),
            occurred_at=timestamp(row[4]),
            captured_at=timestamp(row[5]),
            classification=str(row[6]),
            retention_class=str(row[7]),
            purpose=str(row[8]),
            payload_digest=str(row[9]),
            provenance_refs=tuple(cast(list[str], row[10])),
            artifact_ids=tuple(cast(list[str], row[11])),
            canonical_record_ids=tuple(cast(list[str], row[12])),
            workflow_id=None if row[13] is None else str(row[13]),
            effect_attempt_id=None if row[14] is None else str(row[14]),
            prior_hash=str(row[15]),
            entry_hash=str(row[16]),
        )

    def _set_tenant(self, cursor: Any) -> None:
        cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
