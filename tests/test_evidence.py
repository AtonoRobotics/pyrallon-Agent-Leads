from dataclasses import replace
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from buyer_ops_contracts.evidence import (
    EvidenceIntegrityError,
    build_entry,
    sign_checkpoint,
    verify_artifact,
    verify_chain,
    verify_checkpoint,
)

DIGEST_A = "sha256:" + ("a" * 64)
DIGEST_B = "sha256:" + ("b" * 64)


def _chain():
    first = build_entry(
        tenant_id="tenant-1",
        sequence=1,
        event_id="event-1",
        event_type="canonical_mutation",
        occurred_at="2029-01-01T00:00:00Z",
        captured_at="2029-01-01T00:00:01Z",
        classification="confidential",
        retention_class="canonical_mutation",
        purpose="audit_reconstruction",
        payload_digest=DIGEST_A,
        provenance_refs=("record-1@1",),
        canonical_record_ids=("record-1",),
    )
    second = build_entry(
        tenant_id="tenant-1",
        sequence=2,
        event_id="event-2",
        event_type="provider_receipt",
        occurred_at="2029-01-01T00:01:00Z",
        captured_at="2029-01-01T00:01:01Z",
        classification="confidential",
        retention_class="provider_receipt",
        purpose="effect_reconciliation",
        payload_digest=DIGEST_B,
        provenance_refs=("provider-receipt-1",),
        effect_attempt_id="effect-1",
        prior_hash=first.entry_hash,
    )
    return [first, second]


def test_chain_and_signed_checkpoint_verify() -> None:
    entries = _chain()
    head = verify_chain(entries)
    private_key = Ed25519PrivateKey.generate()
    checkpoint = sign_checkpoint(
        tenant_id="tenant-1",
        through_sequence=2,
        head_hash=head,
        signer_key_id="evidence-signing-key-1",
        private_key=private_key,
        signed_at=datetime(2029, 1, 1, tzinfo=UTC),
    )
    verify_checkpoint(checkpoint, private_key.public_key())


@pytest.mark.parametrize(
    "entries",
    [
        lambda chain: [replace(chain[0], purpose="altered"), chain[1]],
        lambda chain: [chain[1], chain[0]],
        lambda chain: [chain[1]],
    ],
)
def test_chain_detects_alteration_reorder_and_removal(entries) -> None:
    with pytest.raises(EvidenceIntegrityError):
        verify_chain(entries(_chain()))


def test_checkpoint_detects_signature_mutation() -> None:
    key = Ed25519PrivateKey.generate()
    checkpoint = sign_checkpoint(
        tenant_id="tenant-1",
        through_sequence=2,
        head_hash=verify_chain(_chain()),
        signer_key_id="key-1",
        private_key=key,
    )
    with pytest.raises(EvidenceIntegrityError):
        verify_checkpoint(replace(checkpoint, through_sequence=3), key.public_key())


def test_artifact_digest_mismatch_is_detected() -> None:
    with pytest.raises(EvidenceIntegrityError, match="artifact digest mismatch"):
        verify_artifact(b"changed artifact", DIGEST_A)
