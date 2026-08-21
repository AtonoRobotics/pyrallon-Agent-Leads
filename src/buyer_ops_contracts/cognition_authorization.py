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
import urllib.error
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .connector_authorization import HttpClient, urllib_http
from .errors import SetupRejected


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


PROVIDERS = {
    "openai.chatgpt": {
        "provider_id": "openai",
        "auth_class": "subscription_oauth",
        "billing_class": "subscription",
        "subject_type": "entitled_user",
    },
    "openai.api": {
        "provider_id": "openai",
        "auth_class": "metered_api",
        "billing_class": "metered",
        "subject_type": "service_identity",
        "models_url": "https://api.openai.com/v1/models",
    },
    "xai.api": {
        "provider_id": "xai",
        "auth_class": "metered_api",
        "billing_class": "metered",
        "subject_type": "service_identity",
        "models_url": "https://api.x.ai/v1/models",
    },
    "anthropic.api": {
        "provider_id": "anthropic",
        "auth_class": "metered_api",
        "billing_class": "metered",
        "subject_type": "service_identity",
        "models_url": "https://api.anthropic.com/v1/models",
    },
    "local.openai_compatible": {
        "provider_id": "local",
        "auth_class": "local_endpoint",
        "billing_class": "internal",
        "subject_type": "workload",
    },
}


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
        endpoint = os.environ.get("OPENAI_CHATGPT_DEVICE_CODE_URL", "").strip()
        if not endpoint:
            raise SetupRejected(
                "configuration_incomplete", "OPENAI_CHATGPT_DEVICE_CODE_URL is required"
            )
        session_id = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        status, response = self._request(
            "POST",
            endpoint,
            headers={"accept": "application/json", "content-type": "application/json"},
            data=b"{}",
            timeout=15.0,
        )
        if status < 200 or status >= 300 or not isinstance(response, dict):
            raise SetupRejected("provider_rejected", "ChatGPT device authorization was rejected")
        device_code = str(response.get("device_auth_id") or response.get("device_code") or "")
        user_code = str(response.get("user_code") or "")
        verification_uri = str(
            response.get("verification_url") or response.get("verification_uri") or ""
        )
        if not device_code or not user_code or not verification_uri:
            raise SetupRejected("provider_rejected", "ChatGPT device response is incomplete")
        expires_in = _positive_int(response.get("expires_in"), default=900)
        interval = _positive_int(response.get("interval"), default=5)
        expires_at = self._clock() + timedelta(seconds=expires_in)
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    INSERT INTO cognitive_oauth_sessions (
                        tenant_id, session_id, actor_id, provider_id, device_code,
                        code_verifier, user_code, verification_uri, poll_interval_seconds,
                        expires_at
                    ) VALUES (%s, %s, %s, 'openai.chatgpt', %s, %s, %s, %s, %s, %s)
                    """.strip(),
                    (
                        self._tenant_id,
                        session_id,
                        self._actor_id,
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
            "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
            "pollIntervalSeconds": interval,
        }

    def poll_chatgpt_device(self, session_id: str) -> dict[str, Any]:
        endpoint = os.environ.get("OPENAI_CHATGPT_DEVICE_TOKEN_URL", "").strip()
        if not endpoint:
            raise SetupRejected(
                "configuration_incomplete", "OPENAI_CHATGPT_DEVICE_TOKEN_URL is required"
            )
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
                if row is None:
                    raise SetupRejected("validation_failed", "cognitive device session not found")
                actor_id, device_code, verifier, user_code, expires_at, consumed_at = row
                if str(actor_id) != self._actor_id:
                    raise SetupRejected("authority_denied", "cognitive device actor mismatch")
                if consumed_at is not None:
                    raise SetupRejected("authority_denied", "cognitive device session is consumed")
                if expires_at <= self._clock():
                    raise SetupRejected("authority_denied", "cognitive device session expired")
                status, response = self._request(
                    "POST",
                    endpoint,
                    headers={"accept": "application/json", "content-type": "application/json"},
                    data=json.dumps(
                        {
                            "device_auth_id": str(device_code),
                            "device_code": str(device_code),
                            "user_code": str(user_code),
                            "code_verifier": str(verifier),
                        }
                    ).encode(),
                    timeout=15.0,
                )
                if (
                    status in {400, 428}
                    and isinstance(response, dict)
                    and response.get("error")
                    in {
                        "authorization_pending",
                        "slow_down",
                    }
                ):
                    self._connection.commit()
                    return {"sessionId": session_id, "state": "pending"}
                if status < 200 or status >= 300 or not isinstance(response, dict):
                    raise SetupRejected("provider_rejected", "ChatGPT device token exchange failed")
                token = str(response.get("access_token") or "")
                if not token:
                    raise SetupRejected("provider_rejected", "ChatGPT token response is incomplete")
                identity = self._bind_credential_on(
                    cursor,
                    connector_id="openai.chatgpt",
                    provider_id="openai",
                    auth_class="subscription_oauth",
                    billing_class="subscription",
                    provider_account_ref=str(response.get("account_id") or self._actor_id),
                    secret=token,
                    model_ids=_string_list(response.get("models")) or ["chatgpt"],
                    subject_type="entitled_user",
                    expires_at=_expiry(self._clock(), response.get("expires_in")),
                )
                cursor.execute(
                    "UPDATE cognitive_oauth_sessions SET consumed_at = %s WHERE tenant_id = %s AND session_id = %s",
                    (self._clock(), self._tenant_id, session_id),
                )
            self._connection.commit()
        except SetupRejected:
            self._connection.rollback()
            raise
        except Exception:
            self._connection.rollback()
            raise
        return {"sessionId": session_id, **identity, "state": "bound"}

    def bind_metered(
        self,
        *,
        connector_id: str,
        api_key: str,
    ) -> dict[str, str]:
        provider = PROVIDERS.get(connector_id)
        if provider is None or provider.get("auth_class") != "metered_api":
            raise SetupRejected("validation_failed", "unknown metered cognition connector")
        if not api_key.strip():
            raise SetupRejected("validation_failed", "api_key is required")
        models_url = str(provider["models_url"])
        status, response = self._request(
            "GET",
            models_url,
            headers={"accept": "application/json", "authorization": f"Bearer {api_key}"},
            data=None,
            timeout=15.0,
        )
        if status < 200 or status >= 300 or not isinstance(response, dict):
            raise SetupRejected("provider_rejected", "metered provider credential is invalid")
        models = _model_ids(response)
        if not models:
            raise SetupRejected("provider_rejected", "metered provider returned no models")
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                result = self._bind_credential_on(
                    cursor,
                    connector_id=connector_id,
                    provider_id=str(provider["provider_id"]),
                    auth_class=str(provider["auth_class"]),
                    billing_class=str(provider["billing_class"]),
                    provider_account_ref=f"{provider['provider_id']}:{self._actor_id}",
                    secret=api_key,
                    model_ids=models,
                    subject_type=str(provider["subject_type"]),
                    expires_at=None,
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return result

    def bind_local(
        self,
        *,
        base_url: str,
        model_id: str,
        token: str = "",
    ) -> dict[str, str]:
        base_url = base_url.strip().rstrip("/")
        model_id = model_id.strip()
        if not base_url or not model_id or not base_url.startswith(("http://", "https://")):
            raise SetupRejected("validation_failed", "base_url and model_id are required")
        headers = {"accept": "application/json"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        status, response = self._request(
            "GET", f"{base_url}/models", headers=headers, data=None, timeout=15.0
        )
        if status < 200 or status >= 300 or not isinstance(response, dict):
            raise SetupRejected("provider_rejected", "local cognition endpoint is unavailable")
        models = _model_ids(response)
        if model_id not in models:
            raise SetupRejected("provider_rejected", "local model is not advertised by endpoint")
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                result = self._bind_credential_on(
                    cursor,
                    connector_id="local.openai_compatible",
                    provider_id="local",
                    auth_class="local_endpoint",
                    billing_class="internal",
                    provider_account_ref=base_url,
                    secret=token or "local-no-token",
                    model_ids=[model_id],
                    subject_type="workload",
                    expires_at=None,
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return result

    def refuse_unsupported(self, connector_id: str) -> None:
        if connector_id in {"anthropic.claude.subscription", "claude.subscription"}:
            raise SetupRejected(
                "configuration_incomplete",
                "anthropic_subscription_oauth_prohibited",
            )
        if connector_id not in PROVIDERS:
            raise SetupRejected("validation_failed", "unknown cognition connector")

    def _bind_credential_on(
        self,
        cursor: Any,
        *,
        connector_id: str,
        provider_id: str,
        auth_class: str,
        billing_class: str,
        provider_account_ref: str,
        secret: str,
        model_ids: list[str],
        subject_type: str,
        expires_at: datetime | None,
    ) -> dict[str, str]:
        identity_ref = (
            "cognitive:"
            + hashlib.sha256(
                f"{self._tenant_id}:{connector_id}:{provider_account_ref}".encode()
            ).hexdigest()[:32]
        )
        identity = {
            "identityRef": identity_ref,
            "tenantId": self._tenant_id,
            "providerId": provider_id,
            "authClass": auth_class,
            "billingClass": billing_class,
            "subjectType": subject_type,
            "subjectRef": self._actor_id,
            "allowedActionClasses": _configured_action_classes(),
            "allowedModelFamilies": model_ids,
            "concurrencyLimit": _configured_concurrency(),
            "dataPolicyVersion": _configured_data_policy(),
            "state": "active",
        }
        if expires_at is not None:
            identity["expiresAt"] = expires_at.isoformat().replace("+00:00", "Z")
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, secret.encode(), identity_ref.encode())
        cursor.execute(
            """
            INSERT INTO cognitive_credentials (
                tenant_id, identity_ref, provider_id, auth_class, billing_class,
                provider_account_ref, ciphertext, nonce, key_ref, identity_record,
                token_expires_at, bound_at, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'bound')
            ON CONFLICT (tenant_id, identity_ref) DO UPDATE SET
                provider_id = EXCLUDED.provider_id,
                auth_class = EXCLUDED.auth_class,
                billing_class = EXCLUDED.billing_class,
                provider_account_ref = EXCLUDED.provider_account_ref,
                ciphertext = EXCLUDED.ciphertext,
                nonce = EXCLUDED.nonce,
                key_ref = EXCLUDED.key_ref,
                identity_record = EXCLUDED.identity_record,
                token_expires_at = EXCLUDED.token_expires_at,
                bound_at = EXCLUDED.bound_at,
                status = 'bound'
            """.strip(),
            (
                self._tenant_id,
                identity_ref,
                provider_id,
                auth_class,
                billing_class,
                provider_account_ref,
                ciphertext,
                nonce,
                "cognitive-credential-v1",
                identity,
                expires_at,
                self._clock(),
            ),
        )
        return {
            "identityRef": identity_ref,
            "providerId": provider_id,
            "authClass": auth_class,
            "billingClass": billing_class,
            "providerAccountRef": provider_account_ref,
            "state": "active",
        }

    def _set_tenant(self, cursor: Any) -> None:
        cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        data: bytes | None,
        timeout: float,
    ) -> tuple[int, Any]:
        try:
            return self._http.request(method, url, headers=headers, data=data, timeout=timeout)
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise SetupRejected(
                "provider_unavailable", "cognitive provider is unreachable"
            ) from exc


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


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return parsed if parsed > 0 else default


def _expiry(now: datetime, value: Any) -> datetime | None:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    return now + timedelta(seconds=seconds) if seconds > 0 else None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _model_ids(response: dict[str, Any]) -> list[str]:
    data = response.get("data")
    if not isinstance(data, list):
        return []
    return _string_list([item.get("id") for item in data if isinstance(item, dict)])


def _configured_action_classes() -> list[str]:
    values = [
        item.strip()
        for item in os.environ.get("BUYER_OPS_COGNITIVE_ACTION_CLASSES", "").split(",")
        if item.strip()
    ]
    if not values:
        raise SetupRejected(
            "configuration_incomplete", "BUYER_OPS_COGNITIVE_ACTION_CLASSES is required"
        )
    return values


def _configured_concurrency() -> int:
    value = os.environ.get("BUYER_OPS_COGNITIVE_CONCURRENCY", "").strip()
    if not value:
        raise SetupRejected(
            "configuration_incomplete", "BUYER_OPS_COGNITIVE_CONCURRENCY is required"
        )
    try:
        parsed = int(value)
    except ValueError as exc:
        raise SetupRejected(
            "configuration_incomplete", "BUYER_OPS_COGNITIVE_CONCURRENCY must be positive"
        ) from exc
    if parsed <= 0:
        raise SetupRejected(
            "configuration_incomplete", "BUYER_OPS_COGNITIVE_CONCURRENCY must be positive"
        )
    return parsed


def _configured_data_policy() -> str:
    value = os.environ.get("BUYER_OPS_COGNITIVE_DATA_POLICY_VERSION", "").strip()
    if not value:
        raise SetupRejected(
            "configuration_incomplete", "BUYER_OPS_COGNITIVE_DATA_POLICY_VERSION is required"
        )
    return value
