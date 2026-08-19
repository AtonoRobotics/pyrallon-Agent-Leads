from dataclasses import replace
from datetime import UTC, datetime

import pytest

from buyer_ops_contracts.artifacts import EncryptedArtifactStore, StoredObject
from buyer_ops_contracts.evidence import EvidenceIntegrityError


class MemoryObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(
        self,
        *,
        tenant_id: str,
        artifact_id: str,
        content: bytes,
        object_lock_until: datetime | None,
        legal_hold: bool,
    ) -> StoredObject:
        reference = f"opaque://{tenant_id}/{artifact_id}"
        self.objects[reference] = content
        return StoredObject(reference, object_lock_until, legal_hold)

    def get(self, *, encrypted_object_ref: str) -> bytes:
        return self.objects[encrypted_object_ref]

    def delete(self, *, encrypted_object_ref: str) -> None:
        del self.objects[encrypted_object_ref]


def test_artifact_is_encrypted_authenticated_and_digest_verified() -> None:
    backend = MemoryObjectStore()
    store = EncryptedArtifactStore(
        backend,
        encryption_key_ref="kms://tenant-1/evidence-key/7",
        encryption_key=b"k" * 32,
    )
    content = b"source artifact containing private data"
    lock_until = datetime(2033, 1, 1, tzinfo=UTC)
    pointer = store.put(
        tenant_id="tenant-1",
        artifact_id="artifact-1",
        content=content,
        object_lock_until=lock_until,
    )

    assert content not in backend.objects[pointer.encrypted_object_ref]
    assert store.get(tenant_id="tenant-1", pointer=pointer) == content
    assert pointer.encryption_key_ref == "kms://tenant-1/evidence-key/7"
    assert pointer.object_lock_until == lock_until

    with pytest.raises(EvidenceIntegrityError, match="authentication failed"):
        store.get(tenant_id="tenant-2", pointer=pointer)
    with pytest.raises(EvidenceIntegrityError, match="digest mismatch"):
        store.get(
            tenant_id="tenant-1",
            pointer=replace(pointer, artifact_digest="sha256:" + ("0" * 64)),
        )


def test_artifact_ciphertext_tampering_and_deletion() -> None:
    backend = MemoryObjectStore()
    store = EncryptedArtifactStore(
        backend,
        encryption_key_ref="kms://tenant-1/evidence-key/7",
        encryption_key=b"k" * 32,
    )
    pointer = store.put(tenant_id="tenant-1", artifact_id="artifact-1", content=b"artifact")
    ciphertext = backend.objects[pointer.encrypted_object_ref]
    backend.objects[pointer.encrypted_object_ref] = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])

    with pytest.raises(EvidenceIntegrityError, match="authentication failed"):
        store.get(tenant_id="tenant-1", pointer=pointer)

    store.delete(pointer, now=datetime(2034, 1, 1, tzinfo=UTC))
    assert pointer.encrypted_object_ref not in backend.objects
