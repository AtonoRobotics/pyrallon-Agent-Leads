"""Cognitive runtime credential identities.

ChatGPT/Codex subscription uses OpenAI device-code OAuth (subscription_oauth).
OpenAI and xAI platform keys are metered_api. Local OpenAI-compatible endpoints
are local_endpoint. Claude Pro/Max subscription OAuth is refused: Anthropic
restricts those tokens to Claude Code and claude.ai.

Encrypted material stays in cognitive_credentials. CredentialIdentity records
carry opaque references only. Live cognition stays fail-closed until signed
release-activation.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from psycopg.types.json import Jsonb

from .connector_authorization import HttpClient, _b64url, _pkce_verifier, urllib_http
from .structural import validate_record
from .tenant_setup import SetupRejected


class _DefaultHttp:
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

CHATGPT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CHATGPT_DEVICE_CODE_URL = "https://auth.openai.com/api/accounts/deviceauth/usercode"
CHATGPT_DEVICE_TOKEN_URL = "https://auth.openai.com/api/accounts/deviceauth/token"
CHATGPT_TOKEN_URL = "https://auth.openai.com/oauth/token"
CHATGPT_DEVICE_REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"
CHATGPT_SCOPE = "openid profile email offline_access"

PROVIDERS = {
    "openai.chatgpt": {
        "provider_id": "openai",
        "auth_class": "subscription_oauth",
        "billing_class": "subscription",
        "subject_type": "entitled_user",
        "model_family": "approved-codex-family",
        "action_class": "lead_qualification",
    },
    "openai.api": {
        "provider_id": "openai",
        "auth_class": "metered_api",
        "billing_class": "metered",
        "subject_type": "service_identity",
        "model_family": "approved-openai-family",
        "action_class": "lead_qualification",
        "models_url": "https://api.openai.com/v1/models",
    },
    "xai.api": {
        "provider_id": "xai",
        "auth_class": "metered_api",
        "billing_class": "metered",
        "subject_type": "service_identity",
        "model_family": "approved-xai-family",
        "action_class": "lead_qualification",
        "models_url": "https://api.x.ai/v1/models",
    },
    "anthropic.api": {
        "provider_id": "anthropic",
        "auth_class": "metered_api",
        "billing_class": "metered",
        "subject_type": "service_identity",
        "model_family": "approved-anthropic-family",
        "action_class": "lead_qualification",
        "models_url": "https://api.anthropic.com/v1/models",
    },
    "local.openai_compatible": {
        "provider_id": "local",
        "auth_class": "local_endpoint",
        "billing_class": "internal",
        "subject_type": "workload",
        "model_family": "approved-local-family",
        "action_class": "lead_qualification",
    },
}


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _b64url(digest)


def _cognition_key(permit_secret: bytes) -> bytes:
    override = os.environ.get("BUYER_OPS_CREDENTIAL_SECRET", "").encode()
    material = override if len(override) >= 32 else permit_secret
    return HKDF(
        algorithm=SHA256(),
        length=32,
        salt=b"buyer-ops-cognition-v1",
        info=b"cognition-credentials-v1",
    ).derive(material[:64] if len(material) > 64 else material)


class CognitionAuthorization:
    def __init__(
        self,
        connection: Any,
        *,
        tenant_id: str,
        permit_secret: bytes,
        actor_id: str,
        http: HttpClient | None = None,
        clock: Any | None = None,
    ) -> None:
        if len(permit_secret) < 32:
            raise ValueError("permit_secret must contain at least 32 bytes")
        self._connection = connection
        self._tenant_id = tenant_id
        self._actor_id = actor_id
        self._cipher = AESGCM(_cognition_key(permit_secret))
        self._http = http or _DefaultHttp()
        self._clock = clock or (lambda: datetime.now(UTC))

    def identities(self) -> list[dict[str, str]]:
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    SELECT identity_ref, provider_id, auth_class, billing_class,
                           provider_account_ref, status, identity_record
                    FROM cognitive_credentials
                    WHERE tenant_id = %s AND status = 'bound'
                    ORDER BY provider_id, identity_ref
                    """.strip(),
                    (self._tenant_id,),
                )
                rows = cursor.fetchall()
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        out: list[dict[str, str]] = []
        for row in rows:
            record = row[6] if isinstance(row[6], dict) else {}
            out.append(
                {
                    "identityRef": str(row[0]),
                    "providerId": str(row[1]),
                    "authClass": str(row[2]),
                    "billingClass": str(row[3]),
                    "providerAccountRef": str(row[4]),
                    "state": str(record.get("state") or row[5]),
                    "connectorId": _public_connector_id(str(row[1]), str(row[2])),
                }
            )
        return out

    def start_chatgpt_device(self) -> dict[str, str | int]:
        verifier = _pkce_verifier()
        challenge = _pkce_challenge(verifier)
        status, payload = self._json(
            CHATGPT_DEVICE_CODE_URL,
            {
                "client_id": CHATGPT_CLIENT_ID,
                "scope": CHATGPT_SCOPE,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        if status != 200 or not isinstance(payload, dict):
            raise SetupRejected("connector_authorization_failed", "chatgpt_device_start_failed")
        device_code = str(payload.get("device_auth_id") or payload.get("device_code") or "")
        user_code = str(payload.get("user_code") or "")
        verification_uri = str(
            payload.get("verification_uri_complete")
            or payload.get("verification_uri")
            or "https://auth.openai.com/codex/device"
        )
        if not device_code or not user_code:
            raise SetupRejected("connector_authorization_failed", "chatgpt_device_start_failed")
        interval = int(payload.get("interval") or 5)
        session_id = secrets.token_urlsafe(18)
        raw_expiry = payload.get("expires_at")
        if isinstance(raw_expiry, str) and raw_expiry:
            expires_at = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
        else:
            expires_at = self._clock() + timedelta(seconds=int(payload.get("expires_in") or 900))
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    INSERT INTO cognitive_oauth_sessions (
                        tenant_id, session_id, actor_id, provider_id, device_code,
                        code_verifier, user_code, verification_uri, poll_interval_seconds,
                        expires_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """.strip(),
                    (
                        self._tenant_id,
                        session_id,
                        self._actor_id,
                        "openai.chatgpt",
                        device_code,
                        verifier,
                        user_code,
                        verification_uri,
                        interval,
                        expires_at,
                    ),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return {
            "sessionId": session_id,
            "userCode": user_code,
            "verificationUri": verification_uri,
            "expiresAt": _stamp(expires_at),
            "intervalSeconds": interval,
            "connectorId": "openai.chatgpt",
        }

    def poll_chatgpt_device(self, session_id: str) -> dict[str, Any]:
        session = self._session(session_id)
        status, payload = self._json(
            CHATGPT_DEVICE_TOKEN_URL,
            {
                "client_id": CHATGPT_CLIENT_ID,
                "device_auth_id": session["device_code"],
                "user_code": session["user_code"],
            },
        )
        if not isinstance(payload, dict):
            raise SetupRejected("connector_authorization_failed", "chatgpt_device_poll_failed")
        error = _oauth_error(payload)
        if status in {400, 403} and ("authorization_pending" in error or "slow_down" in error):
            return {"status": "pending", "sessionId": session_id}
        if error and "authorization_pending" not in error and "slow_down" not in error:
            raise SetupRejected("connector_authorization_failed", error)
        code = str(payload.get("authorization_code") or "")
        if not code:
            return {"status": "pending", "sessionId": session_id}
        token_status, token = self._form(
            CHATGPT_TOKEN_URL,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": CHATGPT_DEVICE_REDIRECT_URI,
                "client_id": CHATGPT_CLIENT_ID,
                "code_verifier": session["code_verifier"],
            },
        )
        if token_status != 200 or not isinstance(token, dict) or not token.get("access_token"):
            raise SetupRejected("connector_authorization_failed", "chatgpt_token_exchange_failed")
        self._consume_session(session_id)
        account = str(token.get("account_id") or self._actor_id)
        identity = self._bind(
            connector_id="openai.chatgpt",
            account=account,
            secret={
                "access_token": token["access_token"],
                "refresh_token": token.get("refresh_token") or "",
                "id_token": token.get("id_token") or "",
                "token_type": token.get("token_type") or "Bearer",
            },
            expires_in=int(token.get("expires_in") or 3600),
        )
        return {"status": "bound", "identity": identity}

    def bind_metered(self, *, connector_id: str, api_key: str) -> dict[str, str]:
        spec = PROVIDERS.get(connector_id)
        if spec is None or spec["auth_class"] != "metered_api":
            raise SetupRejected("validation_failed", "connector is not a metered model API")
        key = api_key.strip()
        if len(key) < 8:
            raise SetupRejected("validation_failed", "API key is required")
        headers = {"authorization": f"Bearer {key}", "accept": "application/json"}
        if connector_id == "anthropic.api":
            headers = {
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "accept": "application/json",
            }
        status, payload = self._http.request(
            "GET",
            str(spec["models_url"]),
            headers=headers,
            data=None,
            timeout=20,
        )
        if status not in {200, 203}:
            detail = payload.get("error") if isinstance(payload, dict) else status
            raise SetupRejected("connector_authorization_failed", f"metered_api_rejected:{detail}")
        return self._bind(
            connector_id=connector_id,
            account=f"{spec['provider_id']}:****{key[-4:]}",
            secret={"api_key": key},
            expires_in=None,
        )

    def bind_local(self, *, base_url: str, model_id: str, token: str = "") -> dict[str, str]:
        url = base_url.strip().rstrip("/")
        model = model_id.strip()
        if not url.startswith(("http://", "https://")) or not model:
            raise SetupRejected("validation_failed", "local endpoint URL and model id are required")
        headers = {"accept": "application/json"}
        if token.strip():
            headers["authorization"] = f"Bearer {token.strip()}"
        status, _payload = self._http.request(
            "GET",
            f"{url}/models",
            headers=headers,
            data=None,
            timeout=10,
        )
        if status not in {200, 203}:
            raise SetupRejected("connector_authorization_failed", "local_endpoint_rejected")
        return self._bind(
            connector_id="local.openai_compatible",
            account=f"{url}:{model}",
            secret={"base_url": url, "model_id": model, "token": token.strip()},
            expires_in=None,
        )

    def refuse_unsupported(self, connector_id: str) -> None:
        if connector_id in {"anthropic.claude.subscription", "claude.subscription"}:
            raise SetupRejected(
                "configuration_incomplete",
                "anthropic_subscription_oauth_prohibited",
            )
        if connector_id not in PROVIDERS:
            raise SetupRejected("validation_failed", "unknown cognition connector")

    def _bind(
        self,
        *,
        connector_id: str,
        account: str,
        secret: dict[str, str],
        expires_in: int | None,
    ) -> dict[str, str]:
        spec = PROVIDERS[connector_id]
        stamp = self._clock()
        identity_ref = f"cred:{connector_id}:{self._tenant_id}"
        expires_at = stamp + timedelta(seconds=expires_in) if expires_in else None
        identity = {
            "identityRef": identity_ref,
            "tenantId": self._tenant_id,
            "providerId": spec["provider_id"],
            "authClass": spec["auth_class"],
            "billingClass": spec["billing_class"],
            "subjectType": spec["subject_type"],
            "subjectRef": self._actor_id,
            "allowedActionClasses": [spec["action_class"]],
            "allowedModelFamilies": [spec["model_family"]],
            "concurrencyLimit": 1,
            "dataPolicyVersion": "data-policy/unactivated",
            "state": "active",
        }
        if expires_at is not None:
            identity["expiresAt"] = _stamp(expires_at)
        validate_record(identity, "gateway_runtime")
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(
            nonce, json.dumps(secret, separators=(",", ":")).encode(), identity_ref.encode()
        )
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    INSERT INTO cognitive_credentials (
                        tenant_id, identity_ref, provider_id, auth_class, billing_class,
                        provider_account_ref, ciphertext, nonce, key_ref, identity_record,
                        token_expires_at, bound_at, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'bound')
                    ON CONFLICT (tenant_id, identity_ref) DO UPDATE SET
                        provider_account_ref = EXCLUDED.provider_account_ref,
                        ciphertext = EXCLUDED.ciphertext,
                        nonce = EXCLUDED.nonce,
                        identity_record = EXCLUDED.identity_record,
                        token_expires_at = EXCLUDED.token_expires_at,
                        bound_at = EXCLUDED.bound_at,
                        status = 'bound'
                    """.strip(),
                    (
                        self._tenant_id,
                        identity_ref,
                        spec["provider_id"],
                        spec["auth_class"],
                        spec["billing_class"],
                        account,
                        ciphertext,
                        nonce,
                        "cognition-credentials-v1",
                        Jsonb(identity),
                        expires_at,
                        stamp,
                    ),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return {
            "identityRef": identity_ref,
            "providerId": spec["provider_id"],
            "authClass": spec["auth_class"],
            "billingClass": spec["billing_class"],
            "providerAccountRef": account,
            "state": "active",
            "connectorId": connector_id,
        }

    def _form(self, url: str, fields: dict[str, str]) -> tuple[int, Any]:
        return self._http.request(
            "POST",
            url,
            headers={
                "content-type": "application/x-www-form-urlencoded",
                "accept": "application/json",
                "user-agent": "codex-cli",
            },
            data=urlencode(fields).encode(),
            timeout=20,
        )

    def _json(self, url: str, fields: dict[str, str]) -> tuple[int, Any]:
        return self._http.request(
            "POST",
            url,
            headers={
                "content-type": "application/json",
                "accept": "application/json",
                "user-agent": "codex-cli",
            },
            data=json.dumps(fields).encode(),
            timeout=20,
        )

    def _session(self, session_id: str) -> dict[str, str]:
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    SELECT actor_id, device_code, code_verifier, user_code, expires_at, consumed_at
                    FROM cognitive_oauth_sessions
                    WHERE tenant_id = %s AND session_id = %s
                    FOR UPDATE
                    """.strip(),
                    (self._tenant_id, session_id),
                )
                row = cursor.fetchone()
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        if row is None:
            raise SetupRejected("validation_failed", "oauth session missing")
        if str(row[0]) != self._actor_id:
            raise SetupRejected("authority_denied", "oauth actor mismatch")
        if row[5] is not None:
            raise SetupRejected("validation_failed", "oauth session already consumed")
        expires = row[4]
        if hasattr(expires, "astimezone") and expires.astimezone(UTC) <= self._clock():
            raise SetupRejected("validation_failed", "oauth session expired")
        return {
            "device_code": str(row[1]),
            "code_verifier": str(row[2]),
            "user_code": str(row[3]),
        }

    def _consume_session(self, session_id: str) -> None:
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    UPDATE cognitive_oauth_sessions
                    SET consumed_at = %s
                    WHERE tenant_id = %s AND session_id = %s
                    """.strip(),
                    (self._clock(), self._tenant_id, session_id),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def _set_tenant(self, cursor: Any) -> None:
        cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))


def _oauth_error(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("code") or error.get("message") or "")
    if isinstance(error, str):
        return error
    return ""


def _public_connector_id(provider_id: str, auth_class: str) -> str:
    if provider_id == "openai" and auth_class == "subscription_oauth":
        return "openai.chatgpt"
    if provider_id == "openai":
        return "openai.api"
    if provider_id == "xai":
        return "xai.api"
    if provider_id == "anthropic":
        return "anthropic.api"
    return "local.openai_compatible"
