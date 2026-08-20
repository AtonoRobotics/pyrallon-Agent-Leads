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

import os
from datetime import UTC, datetime
from typing import Any, NoReturn

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
        self._credential_identity_admission_unavailable()

    def poll_chatgpt_device(self, session_id: str) -> dict[str, Any]:
        del session_id
        self._credential_identity_admission_unavailable()

    def bind_metered(
        self,
        *,
        connector_id: str,
        api_key: str,
    ) -> dict[str, str]:
        del connector_id, api_key
        self._credential_identity_admission_unavailable()

    def bind_local(
        self,
        *,
        base_url: str,
        model_id: str,
        token: str = "",
    ) -> dict[str, str]:
        del base_url, model_id, token
        self._credential_identity_admission_unavailable()

    def refuse_unsupported(self, connector_id: str) -> None:
        if connector_id in {"anthropic.claude.subscription", "claude.subscription"}:
            raise SetupRejected(
                "configuration_incomplete",
                "anthropic_subscription_oauth_prohibited",
            )
        if connector_id not in PROVIDERS:
            raise SetupRejected("validation_failed", "unknown cognition connector")

    @staticmethod
    def _credential_identity_admission_unavailable() -> NoReturn:
        raise SetupRejected(
            "configuration_incomplete",
            "credential_identity_admission_unavailable",
        )

    def _set_tenant(self, cursor: Any) -> None:
        cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))


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
