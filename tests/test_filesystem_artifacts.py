from datetime import UTC, datetime

import pytest

from buyer_ops_contracts.artifacts import (
    EncryptedArtifactStore,
    FilesystemObjectStore,
    ObjectLockUnsupported,
)


def test_filesystem_adapter_persists_only_encrypted_opaque_blob(tmp_path) -> None:
    backend = FilesystemObjectStore(tmp_path / "objects")
    store = EncryptedArtifactStore(
        backend,
        encryption_key_ref="kms://tenant-1/evidence-key/1",
        encryption_key=b"k" * 32,
    )
    content = b"private source artifact"
    pointer = store.put(tenant_id="tenant-1", artifact_id="artifact-1", content=content)

    assert "tenant-1" not in pointer.encrypted_object_ref
    assert "artifact-1" not in pointer.encrypted_object_ref
    stored = backend.get(encrypted_object_ref=pointer.encrypted_object_ref)
    assert content not in stored
    assert store.get(tenant_id="tenant-1", pointer=pointer) == content
    assert oct((tmp_path / "objects").stat().st_mode & 0o777) == "0o700"

    with pytest.raises(FileExistsError, match="cannot be overwritten"):
        store.put(tenant_id="tenant-1", artifact_id="artifact-1", content=b"replacement")

    store.delete(pointer, now=datetime(2030, 1, 1, tzinfo=UTC))
    with pytest.raises(FileNotFoundError):
        backend.get(encrypted_object_ref=pointer.encrypted_object_ref)


def test_filesystem_adapter_rejects_unenforceable_worm_policy(tmp_path) -> None:
    backend = FilesystemObjectStore(tmp_path / "objects")
    store = EncryptedArtifactStore(
        backend,
        encryption_key_ref="kms://tenant-1/evidence-key/1",
        encryption_key=b"k" * 32,
    )
    with pytest.raises(ObjectLockUnsupported):
        store.put(
            tenant_id="tenant-1",
            artifact_id="artifact-locked",
            content=b"locked",
            object_lock_until=datetime(2035, 1, 1, tzinfo=UTC),
        )
