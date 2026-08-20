"""Inactive connector OAuth configuration and callback-security foundations.

Platform application secrets, signed-state validation, PKCE, redirect validation,
and read-only binding inventory remain available. Connector authorization admission
fails closed until a governing credential-to-grant binding contract is published.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .errors import SetupRejected


class HttpClient(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        data: bytes | None,
        timeout: float,
    ) -> tuple[int, Any]: ...


def urllib_http(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    data: bytes | None,
    timeout: float,
) -> tuple[int, Any]:
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            body = response.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        body = exc.read()
    text = body.decode()
    try:
        return status, json.loads(text) if text else {}
    except json.JSONDecodeError:
        return status, text


class _UrllibHttpClient:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        data: bytes | None,
        timeout: float,
    ) -> tuple[int, Any]:
        return urllib_http(method, url, headers=headers, data=data, timeout=timeout)


DEFAULT_HTTP_CLIENT = _UrllibHttpClient()


def _now() -> datetime:
    return datetime.now(UTC)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _pkce_verifier() -> str:
    return _b64url(secrets.token_bytes(48))


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return _b64url(digest)


def parse_oauth_state(permit_secret: bytes, state: str, *, now: datetime) -> tuple[str, str]:
    parts = state.split(".")
    if len(parts) != 4:
        raise SetupRejected("validation_failed", "oauth state is malformed")
    tenant_id, session_id, exp, digest = parts
    payload = f"{tenant_id}.{session_id}.{exp}"
    expected = hmac.new(permit_secret, payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, digest):
        raise SetupRejected("authority_denied", "oauth state is not authentic")
    if int(exp) <= int(now.timestamp()):
        raise SetupRejected("authority_denied", "oauth state expired")
    return tenant_id, session_id


class ConnectorAuthorization:
    def __init__(
        self,
        connection: Any,
        *,
        tenant_id: str,
        permit_secret: bytes,
        oauth_clients: dict[str, dict[str, str]],
        http: HttpClient = DEFAULT_HTTP_CLIENT,
        clock: Any = _now,
    ) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if len(permit_secret) < 32:
            raise ValueError("permit_secret must contain at least 32 bytes")
        self._connection = connection
        self._tenant_id = tenant_id
        self._permit_secret = permit_secret
        self._clock = clock
        del oauth_clients, http

    def _set_tenant(self, cursor: Any) -> None:
        cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))

    def _sign_state(self, session_id: str, expires_at: datetime) -> str:
        payload = f"{self._tenant_id}.{session_id}.{int(expires_at.timestamp())}"
        digest = hmac.new(self._permit_secret, payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{digest}"

    def parse_state(self, state: str) -> tuple[str, str]:
        return parse_oauth_state(self._permit_secret, state, now=self._clock())

    def start_oauth(
        self,
        *,
        actor_id: str,
        connector_id: str,
        redirect_uri: str,
        return_origin: str = "",
    ) -> dict[str, str]:
        del actor_id, connector_id, redirect_uri, return_origin
        raise SetupRejected(
            "configuration_incomplete",
            "connector authorization admission contract is unavailable",
        )

    def complete_oauth(
        self,
        *,
        code: str,
        state: str,
        actor_id: str,
        account_sid: str = "",
    ) -> dict[str, Any]:
        del code, state, actor_id, account_sid
        raise SetupRejected(
            "configuration_incomplete",
            "connector authorization admission contract is unavailable",
        )

    def bindings(self) -> dict[str, dict[str, str]]:
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    SELECT grant_id, connector_id, provider_account_ref, status
                    FROM connector_credentials
                    WHERE tenant_id = %s
                    """.strip(),
                    (self._tenant_id,),
                )
                rows = cursor.fetchall()
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return {
            str(row[0]): {
                "grant_id": str(row[0]),
                "connector_id": str(row[1]),
                "provider_account_ref": str(row[2]),
                "authorization": str(row[3]),
            }
            for row in rows
        }


class PlatformOAuthStore:
    """Google/Microsoft OAuth application credentials, set from the operator UI."""

    def __init__(self, connection: Any, *, permit_secret: bytes) -> None:
        if len(permit_secret) < 32:
            raise ValueError("permit_secret must contain at least 32 bytes")
        self._connection = connection
        self._permit_secret = permit_secret
        self._cipher = AESGCM(_platform_key(permit_secret))

    def save(
        self,
        *,
        issuer: str,
        client_id: str,
        client_secret: str,
        directory_id: str | None = None,
    ) -> dict[str, str]:
        if issuer not in {"google", "microsoft", "twilio"}:
            raise SetupRejected("validation_failed", "issuer must be google, microsoft, or twilio")
        client_id = client_id.strip()
        client_secret = client_secret.strip()
        if issuer == "twilio":
            if not client_id:
                raise SetupRejected("validation_failed", "Twilio Connect App SID is required")
            client_secret = client_secret or "none"
        elif not client_id or not client_secret:
            raise SetupRejected("validation_failed", "OAuth client id and secret are required")
        if issuer == "microsoft" and not (directory_id or "").strip():
            raise SetupRejected("validation_failed", "Microsoft OAuth directory is required")
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, client_secret.encode(), issuer.encode())
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO platform_oauth_clients (
                        issuer, client_id, directory_id, ciphertext, nonce, key_ref, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, clock_timestamp())
                    ON CONFLICT (issuer) DO UPDATE SET
                        client_id = EXCLUDED.client_id,
                        directory_id = EXCLUDED.directory_id,
                        ciphertext = EXCLUDED.ciphertext,
                        nonce = EXCLUDED.nonce,
                        key_ref = EXCLUDED.key_ref,
                        updated_at = clock_timestamp()
                    """.strip(),
                    (
                        issuer,
                        client_id,
                        (directory_id or "").strip() or None,
                        ciphertext,
                        nonce,
                        "platform-oauth-v1",
                    ),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return {"issuer": issuer, "clientId": client_id, "configured": "true"}

    def list_public(self) -> list[dict[str, str]]:
        env = oauth_clients_from_env()
        issuers = ("google", "microsoft", "twilio")
        rows = {
            issuer: {"issuer": issuer, "clientId": "", "configured": "false"} for issuer in issuers
        }
        for issuer, client in env.items():
            if not client.get("client_id"):
                continue
            if issuer != "twilio" and not client.get("client_secret"):
                continue
            if issuer == "microsoft" and not client.get("directory_id"):
                continue
            rows[issuer] = {
                "issuer": issuer,
                "clientId": client["client_id"],
                "configured": "true",
            }
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    "SELECT issuer, client_id, directory_id "
                    "FROM platform_oauth_clients ORDER BY issuer"
                )
                for issuer, client_id, directory_id in cursor.fetchall():
                    if str(issuer) not in rows:
                        continue
                    if str(issuer) == "microsoft" and not str(directory_id or "").strip():
                        rows["microsoft"] = {
                            "issuer": "microsoft",
                            "clientId": "",
                            "configured": "false",
                        }
                        continue
                    rows[str(issuer)] = {
                        "issuer": str(issuer),
                        "clientId": str(client_id),
                        "configured": "true",
                    }
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return [rows[issuer] for issuer in issuers]

    def client_for(self, issuer: str) -> dict[str, str]:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT client_id, directory_id, ciphertext, nonce
                    FROM platform_oauth_clients WHERE issuer = %s
                    """.strip(),
                    (issuer,),
                )
                row = cursor.fetchone()
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        if row is None:
            return oauth_clients_from_env().get(issuer) or {}
        secret = self._cipher.decrypt(bytes(row[3]), bytes(row[2]), issuer.encode()).decode()
        return {
            "client_id": str(row[0]),
            "client_secret": secret,
            "directory_id": str(row[1] or ""),
        }

    def material(self) -> dict[str, dict[str, str]]:
        out = oauth_clients_from_env()
        if not out["microsoft"].get("directory_id"):
            out["microsoft"] = {}
        for issuer in ("google", "microsoft"):
            stored = self.client_for(issuer)
            if (
                stored.get("client_id")
                and stored.get("client_secret")
                and (issuer != "microsoft" or str(stored.get("directory_id", "")).strip())
            ):
                out[issuer] = stored
            elif issuer == "microsoft" and stored.get("client_id"):
                out[issuer] = {}
        return out


def _platform_key(permit_secret: bytes) -> bytes:
    override = os.environ.get("BUYER_OPS_CREDENTIAL_SECRET", "").encode()
    material = override if len(override) >= 32 else permit_secret
    return HKDF(
        algorithm=SHA256(),
        length=32,
        salt=b"buyer-ops-platform-oauth",
        info=b"platform-oauth-v1",
    ).derive(material[:64] if len(material) > 64 else material)


CONNECTOR_CALLBACK_PATH = "/api/connectors/callback"


def operator_public_origin() -> str:
    return os.environ.get("OPERATOR_PUBLIC_URL", "").strip().rstrip("/")


def canonical_connector_redirect(issuer: str, requested: str) -> str:
    """Google/Microsoft see one redirect URI: OPERATOR_PUBLIC_URL, not the browser tab."""
    public = operator_public_origin()
    if issuer in {"google", "microsoft"} and public:
        return f"{public}{CONNECTOR_CALLBACK_PATH}"
    uri = requested.strip()
    if not uri.startswith(("https://", "http://")):
        raise SetupRejected("validation_failed", "redirect_uri must be an http(s) URL")
    if issuer in {"google", "microsoft"}:
        _require_provider_redirect(issuer, uri)
    return uri


def _return_origin_allowed(origin: str) -> bool:
    parsed = urlparse(origin)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        return False
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    public = os.environ.get("OPERATOR_PUBLIC_URL", "").strip().rstrip("/").lower()
    if public and origin.lower() == public:
        return True
    if host.endswith(".ts.net"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def _require_provider_redirect(issuer: str, redirect_uri: str) -> None:
    parsed = urlparse(redirect_uri)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and host:
        return
    if parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}:
        return
    raise SetupRejected(
        "validation_failed",
        f"{issuer} http OAuth redirects must use 127.0.0.1 or localhost. "
        "Configure OPERATOR_PUBLIC_URL with an HTTPS origin for a non-loopback deployment. "
        "Google rejects private LAN addresses such as 192.168.x.x with invalid_request.",
    )


def oauth_clients_from_env() -> dict[str, dict[str, str]]:
    return {
        "google": {
            "client_id": os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip(),
            "client_secret": os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip(),
        },
        "microsoft": {
            "client_id": os.environ.get("MICROSOFT_OAUTH_CLIENT_ID", "").strip(),
            "client_secret": os.environ.get("MICROSOFT_OAUTH_CLIENT_SECRET", "").strip(),
            "directory_id": os.environ.get("MICROSOFT_OAUTH_TENANT_ID", "").strip(),
        },
        "twilio": {
            "client_id": os.environ.get("TWILIO_CONNECT_APP_SID", "").strip(),
            "client_secret": "",
        },
    }
