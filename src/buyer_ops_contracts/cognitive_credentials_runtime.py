"""Tenant-scoped decryption of cognitive credentials at the runtime edge."""

from __future__ import annotations

from contextvars import ContextVar, Token
from datetime import UTC, datetime

import psycopg
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .cognition_authorization import _cognition_key


class PostgresCognitiveCredentialResolver:
    """Resolve one bound token without exposing it to route configuration or logs."""

    def __init__(self, dsn: str, *, permit_secret: bytes) -> None:
        if len(permit_secret) < 32:
            raise ValueError("permit_secret must contain at least 32 bytes")
        self._dsn = dsn
        self._cipher = AESGCM(_cognition_key(permit_secret))
        self._tenant: ContextVar[str | None] = ContextVar(
            "buyer_ops_cognitive_tenant", default=None
        )

    def set_tenant(self, tenant_id: str) -> Token[str | None]:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        return self._tenant.set(tenant_id)

    def reset(self, token: Token[str | None]) -> None:
        self._tenant.reset(token)

    def __call__(self, identity_ref: str) -> str:
        tenant_id = self._tenant.get()
        if not tenant_id or not identity_ref:
            return ""
        connection = psycopg.connect(self._dsn)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('app.tenant_id', %s, true)",
                    (tenant_id,),
                )
                cursor.execute(
                    """
                    SELECT ciphertext, nonce, token_expires_at, status
                    FROM cognitive_credentials
                    WHERE tenant_id = %s AND identity_ref = %s
                    """.strip(),
                    (tenant_id, identity_ref),
                )
                row = cursor.fetchone()
            connection.commit()
        finally:
            connection.close()
        if row is None:
            return ""
        ciphertext, nonce, expires_at, status = row
        now = datetime.now(UTC)
        if str(status) != "bound" or (expires_at is not None and expires_at <= now):
            return ""
        try:
            return self._cipher.decrypt(
                bytes(nonce), bytes(ciphertext), identity_ref.encode()
            ).decode()
        except (InvalidTag, TypeError, ValueError):
            return ""
