"""Tenant-bound OAuth authorization and encrypted connector credential binding."""

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
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlparse

from cryptography.exceptions import InvalidTag
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


@dataclass(frozen=True, slots=True)
class _OAuthProvider:
    issuer: str
    client_id: str
    client_secret: str
    scopes: tuple[str, ...]
    authorize_url: str
    token_url: str
    identity_url: str | None = None
    authorize_parameters: dict[str, str] = field(default_factory=dict)
    token_parameters: dict[str, str] = field(default_factory=dict)


def _provider_for_connector(
    connector_id: str, clients: dict[str, dict[str, str]]
) -> _OAuthProvider:
    normalized = connector_id.strip().lower()
    if normalized.startswith("google.workspace"):
        client = clients.get("google") or {}
        scopes: tuple[str, ...] = (
            ("https://www.googleapis.com/auth/gmail.modify",)
            if "email" in normalized
            else ("https://www.googleapis.com/auth/calendar",)
            if "calendar" in normalized
            else ("openid", "email", "profile")
        )
        return _configured_provider(
            "google",
            client,
            scopes,
            "https://accounts.google.com/o/oauth2/v2/auth",
            "https://oauth2.googleapis.com/token",
            "https://openidconnect.googleapis.com/v1/userinfo",
            authorize_parameters={"access_type": "offline", "prompt": "consent"},
        )
    if normalized.startswith("microsoft.365"):
        client = clients.get("microsoft") or {}
        directory = str(client.get("directory_id") or "").strip()
        if not directory:
            raise SetupRejected("configuration_incomplete", "Microsoft OAuth directory is required")
        scopes = ("offline_access", "openid", "profile", "email", "User.Read")
        if "email" in normalized:
            scopes += ("Mail.ReadWrite", "Mail.Send")
        if "calendar" in normalized:
            scopes += ("Calendars.ReadWrite",)
        return _configured_provider(
            "microsoft",
            client,
            scopes,
            f"https://login.microsoftonline.com/{urllib.parse.quote(directory)}/oauth2/v2.0/authorize",
            f"https://login.microsoftonline.com/{urllib.parse.quote(directory)}/oauth2/v2.0/token",
            "https://graph.microsoft.com/v1.0/me",
        )
    if normalized.startswith("twilio."):
        client = clients.get("twilio") or {}
        authorize_url = os.environ.get("TWILIO_CONNECT_AUTHORIZE_URL", "").strip()
        token_url = os.environ.get("TWILIO_CONNECT_TOKEN_URL", "").strip()
        if not authorize_url or not token_url:
            raise SetupRejected(
                "configuration_incomplete",
                "Twilio Connect authorization and token endpoints are required",
            )
        scopes = tuple(
            value for value in os.environ.get("TWILIO_CONNECT_SCOPES", "").split() if value
        )
        if not scopes:
            raise SetupRejected("configuration_incomplete", "Twilio Connect scopes are required")
        return _configured_provider(
            "twilio",
            client,
            scopes,
            authorize_url,
            token_url,
            os.environ.get("TWILIO_CONNECT_IDENTITY_URL", "").strip() or None,
        )
    if normalized.startswith("docusign") or normalized.startswith("esign.docusign"):
        client = clients.get("docusign") or {}
        return _configured_provider(
            "docusign",
            client,
            ("signature", "impersonation"),
            os.environ.get(
                "DOCUSIGN_OAUTH_AUTHORIZE_URL", "https://account-d.docusign.com/oauth/auth"
            ),
            os.environ.get(
                "DOCUSIGN_OAUTH_TOKEN_URL", "https://account-d.docusign.com/oauth/token"
            ),
            os.environ.get(
                "DOCUSIGN_OAUTH_IDENTITY_URL", "https://account-d.docusign.com/oauth/userinfo"
            )
            or None,
        )
    raise SetupRejected("validation_failed", "connector does not support OAuth authorization")


def _configured_provider(
    issuer: str,
    client: dict[str, str],
    scopes: tuple[str, ...],
    authorize_url: str,
    token_url: str,
    identity_url: str | None,
    *,
    authorize_parameters: dict[str, str] | None = None,
) -> _OAuthProvider:
    client_id = str(client.get("client_id") or "").strip()
    client_secret = str(client.get("client_secret") or "").strip()
    if not client_id or (issuer != "twilio" and not client_secret):
        raise SetupRejected("configuration_incomplete", f"{issuer} OAuth client is not configured")
    return _OAuthProvider(
        issuer,
        client_id,
        client_secret,
        scopes,
        authorize_url,
        token_url,
        identity_url,
        authorize_parameters or {},
    )


def _token_expiry(now: datetime, expires_in: Any) -> datetime | None:
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return now + timedelta(seconds=seconds)


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
        self._oauth_clients = oauth_clients
        self._http = http

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
        if not actor_id:
            raise SetupRejected("authentication_required", "actor is required")
        provider = _provider_for_connector(connector_id, self._oauth_clients)
        grant = self._load_grant(connector_id, actor_id)
        redirect = canonical_connector_redirect(provider.issuer, redirect_uri)
        return_origin = return_origin.strip().rstrip("/")
        if return_origin and not _return_origin_allowed(return_origin):
            raise SetupRejected(
                "validation_failed", "return_origin is not an allowed operator origin"
            )
        session_id = secrets.token_urlsafe(24)
        verifier = _pkce_verifier()
        expires_at = self._clock() + timedelta(minutes=10)
        state = self._sign_state(session_id, expires_at)
        query = urllib.parse.urlencode(
            {
                "client_id": provider.client_id,
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": " ".join(provider.scopes),
                "state": state,
                "code_challenge": _pkce_challenge(verifier),
                "code_challenge_method": "S256",
                **provider.authorize_parameters,
            }
        )
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    INSERT INTO connector_oauth_sessions (
                        tenant_id, session_id, actor_id, connector_id, grant_id,
                        redirect_uri, code_verifier, expires_at, return_origin
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """.strip(),
                    (
                        self._tenant_id,
                        session_id,
                        actor_id,
                        connector_id,
                        grant["id"],
                        redirect,
                        verifier,
                        expires_at,
                        return_origin,
                    ),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return {
            "authorizationUrl": f"{provider.authorize_url}?{query}",
            "state": state,
            "connectorId": connector_id,
            "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
            "returnOrigin": return_origin,
        }

    def complete_oauth(
        self,
        *,
        code: str,
        state: str,
        actor_id: str,
        account_sid: str = "",
    ) -> dict[str, Any]:
        if not code or not actor_id:
            raise SetupRejected("validation_failed", "authorization code and actor are required")
        tenant_id, session_id = self.parse_state(state)
        if tenant_id != self._tenant_id:
            raise SetupRejected("authority_denied", "oauth tenant mismatch")
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    SELECT session_id, actor_id, connector_id, grant_id, redirect_uri,
                           code_verifier, expires_at, consumed_at, return_origin
                    FROM connector_oauth_sessions
                    WHERE tenant_id = %s AND session_id = %s
                    FOR UPDATE
                    """.strip(),
                    (self._tenant_id, session_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise SetupRejected("validation_failed", "oauth session not found")
                (
                    _session_id,
                    expected_actor,
                    connector_id,
                    grant_id,
                    redirect_uri,
                    verifier,
                    expires_at,
                    consumed_at,
                    return_origin,
                ) = row
                if str(expected_actor) != actor_id:
                    raise SetupRejected("authority_denied", "oauth actor mismatch")
                if consumed_at is not None:
                    raise SetupRejected("authority_denied", "oauth session already consumed")
                if expires_at <= self._clock():
                    raise SetupRejected("authority_denied", "oauth session expired")
                provider = _provider_for_connector(str(connector_id), self._oauth_clients)
                token = self._exchange_code(
                    provider,
                    code=code,
                    redirect_uri=str(redirect_uri),
                    verifier=str(verifier),
                )
                if not isinstance(token, dict) or not str(token.get("access_token") or ""):
                    raise SetupRejected("provider_rejected", "provider returned no access token")
                access_token = str(token["access_token"])
                identity = self._provider_identity(provider, access_token, token)
                provider_account_ref = account_sid.strip() or identity
                if not provider_account_ref:
                    raise SetupRejected("provider_rejected", "provider account identity is missing")
                nonce = os.urandom(12)
                cipher = AESGCM(_platform_key(self._permit_secret))
                ciphertext = cipher.encrypt(
                    nonce, access_token.encode(), str(connector_id).encode()
                )
                refresh_token = str(token.get("refresh_token") or "").strip()
                refresh_nonce = os.urandom(12) if refresh_token else None
                refresh_ciphertext = (
                    cipher.encrypt(
                        refresh_nonce, refresh_token.encode(), str(connector_id).encode()
                    )
                    if refresh_nonce is not None
                    else None
                )
                expires = _token_expiry(self._clock(), token.get("expires_in"))
                cursor.execute(
                    """
                    INSERT INTO connector_credentials (
                        tenant_id, grant_id, connector_id, provider, provider_account_ref,
                        scopes, ciphertext, nonce, key_ref, token_expires_at, bound_at, status,
                        refresh_ciphertext, refresh_nonce
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'bound', %s, %s)
                    ON CONFLICT (tenant_id, grant_id) DO UPDATE SET
                        connector_id = EXCLUDED.connector_id,
                        provider = EXCLUDED.provider,
                        provider_account_ref = EXCLUDED.provider_account_ref,
                        scopes = EXCLUDED.scopes,
                        ciphertext = EXCLUDED.ciphertext,
                        nonce = EXCLUDED.nonce,
                        key_ref = EXCLUDED.key_ref,
                        token_expires_at = EXCLUDED.token_expires_at,
                        bound_at = EXCLUDED.bound_at,
                        status = 'bound',
                        refresh_ciphertext = COALESCE(EXCLUDED.refresh_ciphertext, connector_credentials.refresh_ciphertext),
                        refresh_nonce = COALESCE(EXCLUDED.refresh_nonce, connector_credentials.refresh_nonce)
                    """.strip(),
                    (
                        self._tenant_id,
                        str(grant_id),
                        str(connector_id),
                        provider.issuer,
                        provider_account_ref,
                        list(provider.scopes),
                        ciphertext,
                        nonce,
                        "connector-credential-v1",
                        expires,
                        self._clock(),
                        refresh_ciphertext,
                        refresh_nonce,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE connector_oauth_sessions
                    SET consumed_at = %s
                    WHERE tenant_id = %s AND session_id = %s
                    """.strip(),
                    (self._clock(), self._tenant_id, session_id),
                )
            self._connection.commit()
        except SetupRejected:
            self._connection.rollback()
            raise
        except Exception:
            self._connection.rollback()
            raise
        return {
            "connectorId": str(connector_id),
            "grantId": str(grant_id),
            "provider": provider.issuer,
            "providerAccountRef": provider_account_ref,
            "authorization": "bound",
            "returnOrigin": str(return_origin or ""),
        }

    def _load_grant(self, connector_id: str, actor_id: str) -> dict[str, Any]:
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    SELECT record FROM canonical_records_current
                    WHERE tenant_id = %s AND record_type = 'ConnectorGrant'
                      AND record->>'connectorId' = %s
                    ORDER BY record_id
                    """.strip(),
                    (self._tenant_id, connector_id),
                )
                rows = cursor.fetchall()
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        candidates = [row[0] for row in rows if isinstance(row[0], dict)]
        for grant in candidates:
            if (
                grant.get("grantState") in {"pending", "active"}
                and str(grant.get("grantorId")) == actor_id
            ):
                return grant
        raise SetupRejected("authority_denied", "actor has no pending or active connector grant")

    def _exchange_code(
        self,
        provider: _OAuthProvider,
        *,
        code: str,
        redirect_uri: str,
        verifier: str,
    ) -> dict[str, Any]:
        payload = urllib.parse.urlencode(
            {
                "client_id": provider.client_id,
                "client_secret": provider.client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
                **provider.token_parameters,
            }
        ).encode()
        status, response = self._http.request(
            "POST",
            provider.token_url,
            headers={
                "accept": "application/json",
                "content-type": "application/x-www-form-urlencoded",
            },
            data=payload,
            timeout=15.0,
        )
        if status < 200 or status >= 300 or not isinstance(response, dict):
            raise SetupRejected("provider_rejected", "provider authorization exchange failed")
        return response

    def _provider_identity(
        self, provider: _OAuthProvider, access_token: str, token: dict[str, Any]
    ) -> str:
        raw = token.get("sub") or token.get("account_sid") or token.get("user_id")
        if not raw and provider.identity_url:
            status, response = self._http.request(
                "GET",
                provider.identity_url,
                headers={"accept": "application/json", "authorization": f"Bearer {access_token}"},
                data=None,
                timeout=15.0,
            )
            if 200 <= status < 300 and isinstance(response, dict):
                if provider.issuer == "docusign":
                    accounts = response.get("accounts")
                    if isinstance(accounts, list):
                        for account in accounts:
                            if isinstance(account, dict):
                                raw = account.get("account_id") or account.get("accountId")
                                if raw:
                                    break
                    raw = raw or response.get("account_id") or response.get("accountId")
                else:
                    raw = (
                        response.get("email")
                        or response.get("userPrincipalName")
                        or response.get("id")
                    )
        return str(raw or "").strip()

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
        if issuer not in {"google", "microsoft", "twilio", "docusign"}:
            raise SetupRejected(
                "validation_failed", "issuer must be google, microsoft, twilio, or docusign"
            )
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
        issuers = ("google", "microsoft", "twilio", "docusign")
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
        for issuer in ("google", "microsoft", "docusign"):
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
    """OAuth providers with strict redirect registration use the public callback."""
    public = operator_public_origin()
    if issuer in {"google", "microsoft", "docusign"} and public:
        return f"{public}{CONNECTOR_CALLBACK_PATH}"
    uri = requested.strip()
    if not uri.startswith(("https://", "http://")):
        raise SetupRejected("validation_failed", "redirect_uri must be an http(s) URL")
    if issuer in {"google", "microsoft", "docusign"}:
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
        "docusign": {
            "client_id": os.environ.get("DOCUSIGN_OAUTH_CLIENT_ID", "").strip(),
            "client_secret": os.environ.get("DOCUSIGN_OAUTH_CLIENT_SECRET", "").strip(),
        },
    }


def load_connector_credential(
    connection: Any,
    *,
    tenant_id: str,
    grant_id: str,
    connector_id: str,
    permit_secret: bytes,
    now: datetime | None = None,
) -> tuple[str, str, str, str] | None:
    """Decrypt one active tenant-bound provider token for the effect boundary only."""
    if not tenant_id or not grant_id or not connector_id or len(permit_secret) < 32:
        raise ValueError("tenant, grant, connector, and permit secret are required")
    observed_at = (now or datetime.now(UTC)).astimezone(UTC)
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        cursor.execute(
            """
            SELECT connector_id, provider, provider_account_ref, ciphertext, nonce,
                   token_expires_at, status
            FROM connector_credentials
            WHERE tenant_id = %s AND grant_id = %s AND connector_id = %s
            FOR SHARE
            """.strip(),
            (tenant_id, grant_id, connector_id),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    status = str(row[6])
    expiry = row[5]
    if status != "bound" or (expiry is not None and expiry <= observed_at):
        return None
    token = (
        AESGCM(_platform_key(permit_secret))
        .decrypt(bytes(row[4]), bytes(row[3]), connector_id.encode())
        .decode()
    )
    if not token:
        raise SetupRejected("provider_rejected", "stored connector credential is empty")
    return str(row[0]), str(row[1]), str(row[2]), token


def refresh_connector_credential(
    connection: Any,
    *,
    tenant_id: str,
    grant_id: str,
    connector_id: str,
    permit_secret: bytes,
    now: datetime | None = None,
    oauth_clients: dict[str, dict[str, str]] | None = None,
    http: HttpClient = DEFAULT_HTTP_CLIENT,
) -> tuple[str, str, str, str] | None:
    """Refresh an expired OAuth credential and return the same adapter tuple.

    Refresh is performed only for an existing active tenant/grant binding. The
    refresh token and provider response remain outside canonical state.
    """
    if not tenant_id or not grant_id or not connector_id or len(permit_secret) < 32:
        raise ValueError("tenant, grant, connector, and permit secret are required")
    clients = oauth_clients if oauth_clients is not None else oauth_clients_from_env()
    observed_at = (now or datetime.now(UTC)).astimezone(UTC)
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        cursor.execute(
            """
            SELECT connector_id, provider, provider_account_ref, refresh_ciphertext,
                   refresh_nonce, status
            FROM connector_credentials
            WHERE tenant_id = %s AND grant_id = %s AND connector_id = %s
            FOR UPDATE
            """.strip(),
            (tenant_id, grant_id, connector_id),
        )
        row = cursor.fetchone()
    if row is None or str(row[5]) != "bound" or row[3] is None or row[4] is None:
        return None
    provider = _provider_for_connector(connector_id, clients)
    cipher = AESGCM(_platform_key(permit_secret))
    try:
        refresh_token = cipher.decrypt(bytes(row[4]), bytes(row[3]), connector_id.encode()).decode()
    except (InvalidTag, TypeError, ValueError) as exc:
        raise SetupRejected(
            "provider_rejected", "stored OAuth refresh credential is invalid"
        ) from exc
    payload = urllib.parse.urlencode(
        {
            "client_id": provider.client_id,
            "client_secret": provider.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            **provider.token_parameters,
        }
    ).encode()
    status, response = http.request(
        "POST",
        provider.token_url,
        headers={"accept": "application/json", "content-type": "application/x-www-form-urlencoded"},
        data=payload,
        timeout=15.0,
    )
    if status < 200 or status >= 300 or not isinstance(response, dict):
        raise SetupRejected("provider_rejected", "provider OAuth refresh was rejected")
    access_token = str(response.get("access_token") or "").strip()
    if not access_token:
        raise SetupRejected("provider_rejected", "provider OAuth refresh returned no access token")
    access_nonce = os.urandom(12)
    access_ciphertext = cipher.encrypt(access_nonce, access_token.encode(), connector_id.encode())
    new_refresh = str(response.get("refresh_token") or refresh_token).strip()
    refresh_nonce = os.urandom(12)
    refresh_ciphertext = cipher.encrypt(refresh_nonce, new_refresh.encode(), connector_id.encode())
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE connector_credentials
            SET ciphertext = %s, nonce = %s, token_expires_at = %s,
                refresh_ciphertext = %s, refresh_nonce = %s, bound_at = %s, status = 'bound'
            WHERE tenant_id = %s AND grant_id = %s AND connector_id = %s
            """.strip(),
            (
                access_ciphertext,
                access_nonce,
                _token_expiry(observed_at, response.get("expires_in")),
                refresh_ciphertext,
                refresh_nonce,
                observed_at,
                tenant_id,
                grant_id,
                connector_id,
            ),
        )
    connection.commit()
    return str(row[0]), str(row[1]), str(row[2]), access_token
