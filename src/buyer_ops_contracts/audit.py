"""Deterministic tenant evidence export and independent audit verification."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .evidence import (
    EvidenceCheckpoint,
    EvidenceEntry,
    EvidenceIntegrityError,
    verify_chain,
    verify_checkpoint,
)


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"unsupported audit value: {type(value).__name__}")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    ).encode()


@dataclass(frozen=True, slots=True)
class TenantEvidenceExport:
    tenant_id: str
    generated_at: str
    entries: tuple[EvidenceEntry, ...]
    checkpoints: tuple[EvidenceCheckpoint, ...]
    artifact_versions: tuple[dict[str, Any], ...]
    legal_hold_events: tuple[dict[str, Any], ...]
    projection_fences: tuple[dict[str, Any], ...]
    deletion_tombstones: tuple[dict[str, Any], ...]
    invalidation_events: tuple[dict[str, Any], ...]
    package_digest: str = ""

    def digest_material(self) -> dict[str, Any]:
        material = asdict(self)
        material.pop("package_digest")
        return material

    def to_json(self) -> str:
        return _canonical_bytes(asdict(self)).decode()


def build_tenant_export(
    *,
    tenant_id: str,
    generated_at: str,
    entries: tuple[EvidenceEntry, ...],
    checkpoints: tuple[EvidenceCheckpoint, ...],
    artifact_versions: tuple[dict[str, Any], ...],
    legal_hold_events: tuple[dict[str, Any], ...],
    projection_fences: tuple[dict[str, Any], ...],
    deletion_tombstones: tuple[dict[str, Any], ...],
    invalidation_events: tuple[dict[str, Any], ...],
) -> TenantEvidenceExport:
    if not tenant_id:
        raise ValueError("tenant_id is required")
    verify_chain(list(entries))
    package = TenantEvidenceExport(
        tenant_id=tenant_id,
        generated_at=generated_at,
        entries=entries,
        checkpoints=checkpoints,
        artifact_versions=artifact_versions,
        legal_hold_events=legal_hold_events,
        projection_fences=projection_fences,
        deletion_tombstones=deletion_tombstones,
        invalidation_events=invalidation_events,
    )
    digest = f"sha256:{hashlib.sha256(_canonical_bytes(package.digest_material())).hexdigest()}"
    return replace(package, package_digest=digest)


def verify_tenant_export(
    package: TenantEvidenceExport,
    *,
    checkpoint_keys: Mapping[str, Ed25519PublicKey],
) -> None:
    expected = f"sha256:{hashlib.sha256(_canonical_bytes(package.digest_material())).hexdigest()}"
    if package.package_digest != expected:
        raise EvidenceIntegrityError("tenant evidence export digest mismatch")
    head = verify_chain(list(package.entries))
    by_sequence = {entry.sequence: entry.entry_hash for entry in package.entries}
    for checkpoint in package.checkpoints:
        if checkpoint.tenant_id != package.tenant_id:
            raise EvidenceIntegrityError("cross-tenant checkpoint in evidence export")
        if by_sequence.get(checkpoint.through_sequence) != checkpoint.head_hash:
            raise EvidenceIntegrityError("checkpoint does not match exported ledger sequence")
        try:
            public_key = checkpoint_keys[checkpoint.signer_key_id]
        except KeyError as exc:
            raise EvidenceIntegrityError("checkpoint signer key is unavailable") from exc
        verify_checkpoint(checkpoint, public_key)
    if package.entries and package.entries[-1].entry_hash != head:
        raise EvidenceIntegrityError("exported evidence head mismatch")
