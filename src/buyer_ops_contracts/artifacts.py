"""Provider-neutral encrypted object boundary for source artifacts."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .evidence import EvidenceIntegrityError


class ObjectStore(Protocol):
    """Opaque blob storage; implementations must not interpret artifact content."""

    def put(
        self,
        *,
        tenant_id: str,
        artifact_id: str,
        content: bytes,
        object_lock_until: datetime | None,
        legal_hold: bool,
    ) -> StoredObject: ...

    def get(self, *, encrypted_object_ref: str) -> bytes: ...

    def delete(self, *, encrypted_object_ref: str) -> None: ...


@dataclass(frozen=True, slots=True)
class StoredObject:
    encrypted_object_ref: str
    object_lock_until: datetime | None
    legal_hold: bool


class ObjectLockUnsupported(RuntimeError):
    """Raised when deployment policy requests protection an adapter cannot enforce."""


class FilesystemObjectStore:
    """Opaque encrypted-blob store for dedicated deployments without object-lock requirements."""

    _REFERENCE = re.compile(r"^file-object:v1:([0-9a-f]{64}):([0-9a-f]{64})$")

    def __init__(self, root: Path) -> None:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        self._root = root.resolve()

    def put(
        self,
        *,
        tenant_id: str,
        artifact_id: str,
        content: bytes,
        object_lock_until: datetime | None,
        legal_hold: bool,
    ) -> StoredObject:
        if object_lock_until is not None or legal_hold:
            raise ObjectLockUnsupported(
                "filesystem adapter cannot enforce object lock or provider legal hold"
            )
        tenant_digest = hashlib.sha256(tenant_id.encode()).hexdigest()
        artifact_digest = hashlib.sha256(artifact_id.encode()).hexdigest()
        directory = self._root / tenant_digest
        directory.mkdir(mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
        target = directory / artifact_digest
        descriptor, temporary_name = tempfile.mkstemp(prefix=".pending-", dir=directory)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError as exc:
                raise FileExistsError(
                    "artifact object already exists and cannot be overwritten"
                ) from exc
        finally:
            temporary.unlink(missing_ok=True)
        reference = f"file-object:v1:{tenant_digest}:{artifact_digest}"
        return StoredObject(reference, None, False)

    def get(self, *, encrypted_object_ref: str) -> bytes:
        return self._path(encrypted_object_ref).read_bytes()

    def delete(self, *, encrypted_object_ref: str) -> None:
        self._path(encrypted_object_ref).unlink()

    def _path(self, reference: str) -> Path:
        matched = self._REFERENCE.fullmatch(reference)
        if matched is None:
            raise ValueError("invalid filesystem object reference")
        path = (self._root / matched.group(1) / matched.group(2)).resolve()
        if self._root not in path.parents:
            raise ValueError("object reference escapes configured root")
        return path


@dataclass(frozen=True, slots=True)
class ArtifactPointer:
    artifact_id: str
    encrypted_object_ref: str
    encryption_key_ref: str
    artifact_digest: str
    object_lock_until: datetime | None = None
    provider_legal_hold: bool = False


class EncryptedArtifactStore:
    """AES-256-GCM envelope over a provider adapter using externally supplied key material."""

    _FORMAT_VERSION = b"buyer-ops-artifact-v1\x00"
    _NONCE_BYTES = 12

    def __init__(
        self,
        backend: ObjectStore,
        *,
        encryption_key_ref: str,
        encryption_key: bytes,
    ) -> None:
        if not encryption_key_ref:
            raise ValueError("encryption_key_ref is required")
        if len(encryption_key) != 32:
            raise ValueError("artifact encryption requires a 256-bit key")
        self._backend = backend
        self._key_ref = encryption_key_ref
        self._cipher = AESGCM(encryption_key)

    def put(
        self,
        *,
        tenant_id: str,
        artifact_id: str,
        content: bytes,
        object_lock_until: datetime | None = None,
        legal_hold: bool = False,
    ) -> ArtifactPointer:
        if not tenant_id or not artifact_id:
            raise ValueError("tenant_id and artifact_id are required")
        nonce = os.urandom(self._NONCE_BYTES)
        encrypted = self._cipher.encrypt(nonce, content, self._aad(tenant_id, artifact_id))
        blob = self._FORMAT_VERSION + nonce + encrypted
        stored = self._backend.put(
            tenant_id=tenant_id,
            artifact_id=artifact_id,
            content=blob,
            object_lock_until=object_lock_until,
            legal_hold=legal_hold,
        )
        if stored.object_lock_until != object_lock_until or stored.legal_hold != legal_hold:
            raise EvidenceIntegrityError("object-lock readback does not match requested protection")
        return ArtifactPointer(
            artifact_id=artifact_id,
            encrypted_object_ref=stored.encrypted_object_ref,
            encryption_key_ref=self._key_ref,
            artifact_digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
            object_lock_until=stored.object_lock_until,
            provider_legal_hold=stored.legal_hold,
        )

    def get(self, *, tenant_id: str, pointer: ArtifactPointer) -> bytes:
        blob = self._backend.get(encrypted_object_ref=pointer.encrypted_object_ref)
        if not blob.startswith(self._FORMAT_VERSION):
            raise EvidenceIntegrityError("unsupported encrypted artifact format")
        offset = len(self._FORMAT_VERSION)
        nonce = blob[offset : offset + self._NONCE_BYTES]
        ciphertext = blob[offset + self._NONCE_BYTES :]
        if len(nonce) != self._NONCE_BYTES or not ciphertext:
            raise EvidenceIntegrityError("encrypted artifact is truncated")
        try:
            content = self._cipher.decrypt(
                nonce,
                ciphertext,
                self._aad(tenant_id, pointer.artifact_id),
            )
        except InvalidTag as exc:
            raise EvidenceIntegrityError("encrypted artifact authentication failed") from exc
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if digest != pointer.artifact_digest:
            raise EvidenceIntegrityError("source artifact digest mismatch")
        return content

    def delete(self, pointer: ArtifactPointer, *, now: datetime) -> None:
        if pointer.provider_legal_hold:
            raise PermissionError("provider legal hold prohibits artifact deletion")
        if pointer.object_lock_until is not None and now < pointer.object_lock_until:
            raise PermissionError("provider object lock prohibits artifact deletion")
        self._backend.delete(encrypted_object_ref=pointer.encrypted_object_ref)

    @staticmethod
    def _aad(tenant_id: str, artifact_id: str) -> bytes:
        return f"{tenant_id}\x00{artifact_id}".encode()
