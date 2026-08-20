"""Third-party connector authentication and authorization.

Google Workspace and Microsoft 365 use OAuth 2.0 authorization-code + PKCE.
Twilio is verified against the Accounts API with Account SID and auth token.

Tokens are encrypted in connector_credentials. Canonical ConnectorGrant records
receive only grantState. Live send/schedule still require signed release-activation.
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
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlencode, urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .canonical_repository import CanonicalRepository
from .digest import sha256_digest
from .errors import ContractViolation, SetupRejected

PROVIDERS = {
    "google.workspace.email": {
        "issuer": "google",
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "userinfo": "https://www.googleapis.com/oauth2/v2/userinfo",
        "scopes": (
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.readonly "
            "https://www.googleapis.com/auth/userinfo.email"
        ),
        "capabilities": ["read", "send"],
        "grant_scopes": ["mail"],
    },
    "google.workspace.calendar": {
        "issuer": "google",
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "userinfo": "https://www.googleapis.com/oauth2/v2/userinfo",
        "scopes": (
            "https://www.googleapis.com/auth/calendar.events "
            "https://www.googleapis.com/auth/userinfo.email"
        ),
        "capabilities": ["read", "schedule"],
        "grant_scopes": ["calendar"],
    },
    "microsoft.365.email": {
        "issuer": "microsoft",
        "authorize": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "userinfo": "https://graph.microsoft.com/v1.0/me",
        "scopes": "offline_access User.Read Mail.Read Mail.Send",
        "capabilities": ["read", "send"],
        "grant_scopes": ["mail"],
    },
    "microsoft.365.calendar": {
        "issuer": "microsoft",
        "authorize": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "userinfo": "https://graph.microsoft.com/v1.0/me",
        "scopes": "offline_access User.Read Calendars.ReadWrite",
        "capabilities": ["read", "schedule"],
        "grant_scopes": ["calendar"],
    },
    "twilio.sms": {
        "issuer": "twilio",
        "authorize": "https://www.twilio.com/authorize/{client_id}",
        "capabilities": ["read", "send"],
        "grant_scopes": ["sms"],
        "channels": ["twilio.sms"],
    },
    "google.workspace": {
        "issuer": "google",
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "userinfo": "https://www.googleapis.com/oauth2/v2/userinfo",
        "scopes": (
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.readonly "
            "https://www.googleapis.com/auth/calendar.events "
            "https://www.googleapis.com/auth/userinfo.email"
        ),
        "channels": ["google.workspace.email", "google.workspace.calendar"],
    },
    "microsoft.365": {
        "issuer": "microsoft",
        "authorize": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "userinfo": "https://graph.microsoft.com/v1.0/me",
        "scopes": "offline_access User.Read Mail.Read Mail.Send Calendars.ReadWrite",
        "channels": ["microsoft.365.email", "microsoft.365.calendar"],
    },
}


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


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        self._oauth_clients = oauth_clients
        self._http = http
        self._clock = clock
        self._cipher = AESGCM(self._credential_key())

    def _credential_key(self) -> bytes:
        override = os.environ.get("BUYER_OPS_CREDENTIAL_SECRET", "").encode()
        material = override if len(override) >= 32 else self._permit_secret
        return HKDF(
            algorithm=SHA256(),
            length=32,
            salt=b"buyer-ops-connector-credentials",
            info=b"connector-credentials-v1",
        ).derive(material[:64] if len(material) > 64 else material)

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
        spec = PROVIDERS.get(connector_id)
        if spec is None or spec["issuer"] not in {"google", "microsoft", "twilio"}:
            raise SetupRejected("validation_failed", "connector does not use OAuth")
        issuer = str(spec["issuer"])
        redirect_uri = canonical_connector_redirect(issuer, redirect_uri)
        client = self._client_for(issuer)
        channels = list(spec.get("channels") or [connector_id])
        for channel in channels:
            channel_spec = PROVIDERS[channel]
            self._require_or_create_grant(channel, channel_spec)
        grant_id = f"bundle:{connector_id}:{self._tenant_id}"
        session_id = secrets.token_urlsafe(18)
        verifier = _pkce_verifier()
        expires_at = self._clock() + timedelta(minutes=10)
        origin = return_origin.strip().rstrip("/")
        if origin and not _return_origin_allowed(origin):
            raise SetupRejected(
                "validation_failed", "return_origin is not an allowed operator origin"
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
                        grant_id,
                        redirect_uri,
                        verifier,
                        expires_at,
                        origin or None,
                    ),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        state = self._sign_state(session_id, expires_at)
        if spec["issuer"] == "twilio":
            authorize = str(spec["authorize"]).format(client_id=client["client_id"])
            return {
                "authorizationUrl": f"{authorize}?{urlencode({'state': state})}",
                "connectorId": connector_id,
                "grantId": grant_id,
                "expiresAt": _stamp(expires_at),
            }
        authorize = str(spec["authorize"]).format(tenant=client.get("directory_id") or "common")
        params = {
            "client_id": client["client_id"],
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": spec["scopes"],
            "state": state,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "access_type": "offline",
            "include_granted_scopes": "true",
        }
        if spec["issuer"] == "microsoft":
            params.pop("access_type")
            params.pop("include_granted_scopes")
            params["response_mode"] = "query"
            params["prompt"] = "consent"
        return {
            "authorizationUrl": f"{authorize}?{urlencode(params)}",
            "connectorId": connector_id,
            "grantId": grant_id,
            "redirectUri": redirect_uri,
            "expiresAt": _stamp(expires_at),
        }

    def complete_oauth(
        self,
        *,
        code: str,
        state: str,
        actor_id: str,
        account_sid: str = "",
    ) -> dict[str, Any]:
        tenant_id, session_id = self.parse_state(state)
        if tenant_id != self._tenant_id:
            raise SetupRejected("authority_denied", "oauth tenant mismatch")
        session = self._consume_session(session_id, actor_id)
        actor_id = session["actor_id"]
        spec = PROVIDERS[session["connector_id"]]
        issuer = str(spec["issuer"])
        if issuer == "twilio":
            return self._complete_twilio_connect(session, account_sid)
        if not code.strip():
            raise SetupRejected("validation_failed", "authorization code is required")
        client = self._client_for(issuer)
        token_url = str(spec["token"]).format(tenant=client.get("directory_id") or "common")
        body = urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": session["redirect_uri"],
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
                "code_verifier": session["code_verifier"],
            }
        ).encode()
        status, payload = self._http.request(
            "POST",
            token_url,
            headers={
                "content-type": "application/x-www-form-urlencoded",
                "accept": "application/json",
            },
            data=body,
            timeout=20,
        )
        if status != 200 or not isinstance(payload, dict) or not payload.get("access_token"):
            detail = payload.get("error_description") if isinstance(payload, dict) else str(payload)
            raise SetupRejected(
                "connector_authorization_failed",
                f"token endpoint rejected the authorization code: {detail}",
            )
        account = self._provider_account(spec, str(payload["access_token"]))
        token_expires = self._clock() + timedelta(seconds=int(payload.get("expires_in") or 3600))
        bound_scopes = str(payload.get("scope") or spec["scopes"]).split()
        channels = list(spec.get("channels") or [session["connector_id"]])
        grants = []
        for channel in channels:
            channel_spec = PROVIDERS[channel]
            grant = self._require_or_create_grant(channel, channel_spec)
            self._store_binding(
                grant_id=grant["id"],
                connector_id=channel,
                provider=str(spec["issuer"]),
                account=account,
                scopes=bound_scopes,
                secret=payload,
                token_expires_at=token_expires,
            )
            grants.append(self._activate_grant(grant["id"], account))
        return {
            "connectorId": session["connector_id"],
            "grantId": grants[0]["id"] if grants else session["grant_id"],
            "grantState": grants[0]["grantState"] if grants else "pending",
            "authorization": "bound",
            "providerAccountRef": account,
            "channels": channels,
            "returnOrigin": session.get("return_origin") or "",
        }

    def _complete_twilio_connect(self, session: dict[str, str], account_sid: str) -> dict[str, Any]:
        return_origin = session.get("return_origin") or ""
        sid = account_sid.strip()
        if not sid.startswith("AC") or len(sid) < 32:
            raise SetupRejected(
                "connector_authorization_failed",
                "Twilio did not return an authorized AccountSid",
            )
        grant = self._require_or_create_grant("twilio.sms", PROVIDERS["twilio.sms"])
        self._store_binding(
            grant_id=grant["id"],
            connector_id="twilio.sms",
            provider="twilio",
            account=sid,
            scopes=["sms"],
            secret={"account_sid": sid, "grant": "twilio_connect"},
            token_expires_at=None,
        )
        activated = self._activate_grant(grant["id"], sid)
        return {
            "connectorId": "twilio.sms",
            "grantId": grant["id"],
            "grantState": activated["grantState"],
            "authorization": "bound",
            "providerAccountRef": sid,
            "channels": ["twilio.sms"],
            "returnOrigin": return_origin,
        }

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

    def _client_for(self, issuer: str) -> dict[str, str]:
        stored = PlatformOAuthStore(self._connection, permit_secret=self._permit_secret).client_for(
            issuer
        )
        if stored.get("client_id") and (issuer == "twilio" or stored.get("client_secret")):
            return stored
        client = dict(self._oauth_clients.get(issuer) or {})
        env = oauth_clients_from_env().get(issuer) or {}
        if not client.get("client_id"):
            client = env
        if issuer == "twilio":
            if not client.get("client_id"):
                raise SetupRejected("configuration_incomplete", "twilio_oauth_app_required")
            return client
        if not client.get("client_id") or not client.get("client_secret"):
            raise SetupRejected(
                "configuration_incomplete",
                f"{issuer}_oauth_app_required",
            )
        return client

    def _provider_account(self, spec: dict[str, Any], access_token: str) -> str:
        status, payload = self._http.request(
            "GET",
            str(spec["userinfo"]),
            headers={"authorization": f"Bearer {access_token}", "accept": "application/json"},
            data=None,
            timeout=20,
        )
        if status != 200 or not isinstance(payload, dict):
            raise SetupRejected(
                "connector_authorization_failed",
                "provider userinfo rejected the access token",
            )
        account = str(
            payload.get("email") or payload.get("mail") or payload.get("userPrincipalName") or ""
        )
        if not account:
            raise SetupRejected(
                "connector_authorization_failed",
                "provider did not return an account identifier",
            )
        return account

    def _consume_session(self, session_id: str, actor_id: str) -> dict[str, str]:
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
                    raise SetupRejected("authority_denied", "oauth session is unknown")
                if row[7] is not None:
                    raise SetupRejected("authority_denied", "oauth session already consumed")
                if row[6] <= self._clock():
                    raise SetupRejected("authority_denied", "oauth session expired")
                if actor_id and row[1] != actor_id:
                    raise SetupRejected("authority_denied", "oauth session actor mismatch")
                cursor.execute(
                    """
                    UPDATE connector_oauth_sessions
                    SET consumed_at = clock_timestamp()
                    WHERE tenant_id = %s AND session_id = %s AND consumed_at IS NULL
                    """.strip(),
                    (self._tenant_id, session_id),
                )
            self._connection.commit()
        except SetupRejected:
            self._connection.rollback()
            raise
        except Exception:
            self._connection.rollback()
            raise
        return {
            "session_id": str(row[0]),
            "actor_id": str(row[1]),
            "connector_id": str(row[2]),
            "grant_id": str(row[3]),
            "redirect_uri": str(row[4]),
            "code_verifier": str(row[5]),
            "return_origin": str(row[8] or ""),
        }

    def _store_binding(
        self,
        *,
        grant_id: str,
        connector_id: str,
        provider: str,
        account: str,
        scopes: list[str],
        secret: dict[str, Any],
        token_expires_at: datetime | None,
    ) -> None:
        nonce = os.urandom(12)
        plaintext = json.dumps(secret, sort_keys=True, separators=(",", ":")).encode()
        ciphertext = self._cipher.encrypt(nonce, plaintext, self._tenant_id.encode())
        bound_at = self._clock()
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    INSERT INTO connector_credentials (
                        tenant_id, grant_id, connector_id, provider, provider_account_ref,
                        scopes, ciphertext, nonce, key_ref, token_expires_at, bound_at, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'bound')
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
                        status = 'bound'
                    """.strip(),
                    (
                        self._tenant_id,
                        grant_id,
                        connector_id,
                        provider,
                        account,
                        scopes,
                        ciphertext,
                        nonce,
                        "connector-credentials-v1",
                        token_expires_at,
                        bound_at,
                    ),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def _require_or_create_grant(self, connector_id: str, spec: dict[str, Any]) -> dict[str, Any]:
        repo = CanonicalRepository(self._connection, tenant_id=self._tenant_id)
        existing = [
            item
            for item in repo.list_by_type("ConnectorGrant")
            if item.get("connectorId") == connector_id and item.get("status") == "active"
        ]
        if existing:
            return existing[0]
        holders = [
            item
            for item in repo.list_by_type("LicenseHolder")
            if item.get("licenseState") == "active" and item.get("status") == "active"
        ]
        brokerages = repo.list_by_type("Brokerage")
        if len(holders) != 1 or not brokerages:
            raise SetupRejected(
                "configuration_incomplete",
                "admit brokerage and license holder identity before connector authorization",
            )
        stamp = _stamp(self._clock())
        evidence_id = f"evidence:connector:{connector_id}:{secrets.token_hex(4)}"
        digest = sha256_digest(
            {"connectorId": connector_id, "tenantId": self._tenant_id, "at": stamp}
        )
        evidence = {
            "id": evidence_id,
            "tenantId": self._tenant_id,
            "schemaVersion": "buyer-ops/0.3.0",
            "recordType": "Evidence",
            "version": 1,
            "createdAt": stamp,
            "updatedAt": stamp,
            "effectiveFrom": stamp,
            "createdBy": {"actorType": "system_migration", "actorId": f"setup:{self._tenant_id}"},
            "sourceEvidenceIds": [evidence_id],
            "status": "active",
            "sourceType": "system_observation",
            "sourceRef": f"connector-oauth:{connector_id}",
            "digest": digest,
            "retentionClass": "operational",
            "capturedAt": stamp,
            "evidenceState": "current",
        }
        grant = {
            "id": f"grant:{connector_id}:{self._tenant_id}",
            "tenantId": self._tenant_id,
            "schemaVersion": "buyer-ops/0.3.0",
            "recordType": "ConnectorGrant",
            "version": 1,
            "createdAt": stamp,
            "updatedAt": stamp,
            "effectiveFrom": stamp,
            "createdBy": {"actorType": "system_migration", "actorId": f"setup:{self._tenant_id}"},
            "sourceEvidenceIds": [evidence_id],
            "status": "active",
            "connectorId": connector_id,
            "delegatedPrincipalType": "license_holder",
            "delegatedPrincipalId": holders[0]["id"],
            "grantorType": "brokerage",
            "grantorId": brokerages[0]["id"],
            "capabilities": list(spec["capabilities"]),
            "scopes": list(spec["grant_scopes"]),
            "grantedAt": stamp,
            "grantState": "pending",
        }
        try:
            repo.save(evidence)
            return repo.save(grant)
        except ContractViolation as exc:
            raise SetupRejected(
                "validation_failed",
                "; ".join(f"{item.code}: {item.message}" for item in exc.violations),
            ) from exc

    def _activate_grant(self, grant_id: str, account: str) -> dict[str, Any]:
        repo = CanonicalRepository(self._connection, tenant_id=self._tenant_id)
        current = repo.get(grant_id)
        if current is None or current.get("recordType") != "ConnectorGrant":
            raise SetupRejected("validation_failed", "connector grant missing")
        if current.get("grantState") == "active":
            return current
        stamp = _stamp(self._clock())
        evidence_id = f"evidence:bound:{grant_id}:{secrets.token_hex(4)}"
        digest = sha256_digest(
            {"grantId": grant_id, "providerAccountRef": account, "boundAt": stamp}
        )
        evidence = {
            "id": evidence_id,
            "tenantId": self._tenant_id,
            "schemaVersion": "buyer-ops/0.3.0",
            "recordType": "Evidence",
            "version": 1,
            "createdAt": stamp,
            "updatedAt": stamp,
            "effectiveFrom": stamp,
            "createdBy": current["createdBy"],
            "sourceEvidenceIds": [evidence_id],
            "status": "active",
            "sourceType": "provider_receipt",
            "sourceRef": f"oauth:{grant_id}",
            "digest": digest,
            "retentionClass": "operational",
            "capturedAt": stamp,
            "evidenceState": "current",
        }
        successor = dict(current)
        successor["version"] = int(current["version"]) + 1
        successor["updatedAt"] = stamp
        successor["grantState"] = "active"
        successor["sourceEvidenceIds"] = [evidence_id]
        try:
            repo.save(evidence)
            return repo.save(successor, expected_version=int(current["version"]))
        except ContractViolation as exc:
            raise SetupRejected(
                "validation_failed",
                "; ".join(f"{item.code}: {item.message}" for item in exc.violations),
            ) from exc


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
            rows[issuer] = {
                "issuer": issuer,
                "clientId": client["client_id"],
                "configured": "true",
            }
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    "SELECT issuer, client_id FROM platform_oauth_clients ORDER BY issuer"
                )
                for issuer, client_id in cursor.fetchall():
                    if str(issuer) not in rows:
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
            "directory_id": str(row[1] or "common"),
        }

    def material(self) -> dict[str, dict[str, str]]:
        out = oauth_clients_from_env()
        for issuer in ("google", "microsoft"):
            stored = self.client_for(issuer)
            if stored.get("client_id") and stored.get("client_secret"):
                out[issuer] = stored
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
            "directory_id": os.environ.get("MICROSOFT_OAUTH_TENANT_ID", "common").strip()
            or "common",
        },
        "twilio": {
            "client_id": os.environ.get("TWILIO_CONNECT_APP_SID", "").strip(),
            "client_secret": "",
        },
    }
