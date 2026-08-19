"""Tamper-evident, content-minimized evidence primitives for PKT-02."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .errors import ContractViolation, Violation

EvidenceEventType = Literal[
    "material_observation",
    "context_manifest",
    "authority_decision",
    "approval",
    "outbound_communication",
    "inbound_communication",
    "canonical_mutation",
    "external_effect_request",
    "provider_receipt",
    "workflow_transition",
    "correction",
    "deletion",
]

GENESIS_HASH = "sha256:" + ("0" * 64)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class EvidenceIntegrityError(RuntimeError):
    """Raised when ledger order, hashes, signatures, or artifact bytes do not verify."""


@dataclass(frozen=True, slots=True)
class EvidenceEntry:
    tenant_id: str
    sequence: int
    event_id: str
    event_type: EvidenceEventType
    occurred_at: str
    captured_at: str
    classification: str
    retention_class: str
    purpose: str
    payload_digest: str
    provenance_refs: tuple[str, ...]
    artifact_ids: tuple[str, ...] = ()
    canonical_record_ids: tuple[str, ...] = ()
    workflow_id: str | None = None
    effect_attempt_id: str | None = None
    prior_hash: str = GENESIS_HASH
    entry_hash: str = ""

    def hash_material(self) -> dict[str, Any]:
        material = asdict(self)
        material.pop("entry_hash")
        return material


@dataclass(frozen=True, slots=True)
class EvidenceCheckpoint:
    tenant_id: str
    through_sequence: int
    head_hash: str
    signed_at: str
    signer_key_id: str
    signature: str

    def signed_material(self) -> dict[str, str | int]:
        return {
            "tenant_id": self.tenant_id,
            "through_sequence": self.through_sequence,
            "head_hash": self.head_hash,
            "signed_at": self.signed_at,
            "signer_key_id": self.signer_key_id,
        }


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha256(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("evidence timestamp must include an offset")
    return parsed.astimezone(UTC).isoformat()


def build_entry(
    *,
    tenant_id: str,
    sequence: int,
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
    prior_hash: str = GENESIS_HASH,
) -> EvidenceEntry:
    """Build one hash-linked entry without accepting evidence-body or reasoning content."""
    violations: list[Violation] = []
    required = {
        "tenant_id": tenant_id,
        "event_id": event_id,
        "classification": classification,
        "retention_class": retention_class,
        "purpose": purpose,
    }
    for field_name, value in required.items():
        if not value:
            violations.append(Violation("EVIDENCE_FIELD_REQUIRED", field_name, "is required"))
    if sequence < 1:
        violations.append(Violation("EVIDENCE_SEQUENCE", "sequence", "must be positive"))
    if not _DIGEST.fullmatch(payload_digest):
        violations.append(
            Violation("EVIDENCE_DIGEST", "payload_digest", "must be a lowercase SHA-256 digest")
        )
    if not _DIGEST.fullmatch(prior_hash):
        violations.append(
            Violation("EVIDENCE_PRIOR_HASH", "prior_hash", "must be a lowercase SHA-256 digest")
        )
    if not provenance_refs:
        violations.append(Violation("EVIDENCE_PROVENANCE", "provenance_refs", "must not be empty"))
    if violations:
        raise ContractViolation(violations)

    entry = EvidenceEntry(
        tenant_id=tenant_id,
        sequence=sequence,
        event_id=event_id,
        event_type=event_type,
        occurred_at=_timestamp(occurred_at),
        captured_at=_timestamp(captured_at),
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
    return replace(entry, entry_hash=_sha256(_canonical_bytes(entry.hash_material())))


def verify_chain(entries: list[EvidenceEntry]) -> str:
    """Verify sequence, tenant, predecessor, and entry hashes; return the head hash."""
    if not entries:
        return GENESIS_HASH
    tenant_id = entries[0].tenant_id
    prior_hash = GENESIS_HASH
    for expected_sequence, entry in enumerate(entries, start=1):
        if entry.tenant_id != tenant_id:
            raise EvidenceIntegrityError("cross-tenant entry in evidence chain")
        if entry.sequence != expected_sequence:
            raise EvidenceIntegrityError("evidence sequence is missing, inserted, or reordered")
        if entry.prior_hash != prior_hash:
            raise EvidenceIntegrityError("evidence predecessor hash mismatch")
        expected_hash = _sha256(_canonical_bytes(entry.hash_material()))
        if entry.entry_hash != expected_hash:
            raise EvidenceIntegrityError("evidence entry hash mismatch")
        prior_hash = entry.entry_hash
    return prior_hash


def sign_checkpoint(
    *,
    tenant_id: str,
    through_sequence: int,
    head_hash: str,
    signer_key_id: str,
    private_key: Ed25519PrivateKey,
    signed_at: datetime | None = None,
) -> EvidenceCheckpoint:
    if through_sequence < 1 or not _DIGEST.fullmatch(head_hash) or not signer_key_id:
        raise ValueError("checkpoint requires a sequence, SHA-256 head hash, and signer key id")
    timestamp = (signed_at or datetime.now(UTC)).astimezone(UTC).isoformat()
    unsigned = EvidenceCheckpoint(
        tenant_id=tenant_id,
        through_sequence=through_sequence,
        head_hash=head_hash,
        signed_at=timestamp,
        signer_key_id=signer_key_id,
        signature="",
    )
    signature = private_key.sign(_canonical_bytes(unsigned.signed_material()))
    return replace(unsigned, signature=base64.b64encode(signature).decode("ascii"))


def verify_checkpoint(checkpoint: EvidenceCheckpoint, public_key: Ed25519PublicKey) -> None:
    try:
        signature = base64.b64decode(checkpoint.signature, validate=True)
        public_key.verify(signature, _canonical_bytes(checkpoint.signed_material()))
    except (InvalidSignature, ValueError) as exc:
        raise EvidenceIntegrityError("evidence checkpoint signature mismatch") from exc


def verify_artifact(content: bytes, expected_digest: str) -> None:
    if _sha256(content) != expected_digest:
        raise EvidenceIntegrityError("source artifact digest mismatch")
