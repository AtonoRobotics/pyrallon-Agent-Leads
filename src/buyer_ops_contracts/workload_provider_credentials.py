"""Renewable workload credentials for production provider adapters.

The resolver is deliberately limited to provider token endpoints.  It returns
an access token to the direct adapter and never returns, logs, or persists the
private key or credential source material.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class ProviderTokenTransport(Protocol):
    def request(
        self, url: str, *, headers: Mapping[str, str], body: bytes
    ) -> tuple[int, bytes]: ...


class UrllibProviderTokenTransport:
    def request(self, url: str, *, headers: Mapping[str, str], body: bytes) -> tuple[int, bytes]:
        request = urllib.request.Request(url, data=body, method="POST", headers=dict(headers))
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return int(response.status), response.read()
        except urllib.error.HTTPError as exc:
            return int(exc.code), exc.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ValueError("provider workload token endpoint is unavailable") from exc


@dataclass(frozen=True, slots=True)
class ProviderWorkloadIdentity:
    provider: str
    mode: str
    credential_env: str
    client_id: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    subject_env: str | None = None
    token_url: str | None = None
    scopes: tuple[str, ...] = ()

    @classmethod
    def from_value(cls, value: Any) -> ProviderWorkloadIdentity:
        if not isinstance(value, dict):
            raise ValueError("provider workload identity must be an object")
        provider = str(value.get("provider") or "").lower()
        mode = str(value.get("credentialMode") or "").lower()
        credential_env = str(value.get("credentialEnv") or "")
        expected = {
            "google_service_account": "google_calendar",
            "microsoft_client_certificate": "microsoft_graph",
            "docusign_jwt": "docusign",
        }
        if mode not in expected:
            raise ValueError("credentialMode must be a supported workload identity mode")
        if provider != expected[mode]:
            raise ValueError(f"credentialMode {mode} is not valid for provider {provider}")
        if not credential_env:
            raise ValueError("credentialEnv is required for workload identity")
        client_id = _optional(value, "clientId")
        tenant_id = _optional(value, "tenantId")
        user_id = _optional(value, "userId")
        subject_env = _optional(value, "subjectEnv")
        token_url = _optional(value, "tokenUrl")
        scopes_value = value.get("scopes")
        if scopes_value is None:
            scopes = ()
        elif not isinstance(scopes_value, list) or not all(
            isinstance(item, str) and item for item in scopes_value
        ):
            raise ValueError("scopes must be an array of non-empty strings")
        else:
            scopes = tuple(scopes_value)
        if mode == "google_service_account" and not subject_env:
            raise ValueError("google_service_account requires subjectEnv")
        if mode == "microsoft_client_certificate" and (not client_id or not tenant_id):
            raise ValueError("microsoft_client_certificate requires clientId and tenantId")
        if mode == "docusign_jwt" and (not client_id or not user_id):
            raise ValueError("docusign_jwt requires clientId and userId")
        if token_url and not token_url.startswith("https://"):
            raise ValueError("tokenUrl must use HTTPS")
        return cls(
            provider=provider,
            mode=mode,
            credential_env=credential_env,
            client_id=client_id,
            tenant_id=tenant_id,
            user_id=user_id,
            subject_env=subject_env,
            token_url=token_url,
            scopes=scopes,
        )


class ProviderWorkloadCredential:
    """Mints and renews one provider access token from a workload identity."""

    def __init__(
        self,
        identity: ProviderWorkloadIdentity,
        *,
        environment: Mapping[str, str] | None = None,
        transport: ProviderTokenTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._identity = identity
        self._environment = environment if environment is not None else os.environ
        self._transport = transport or UrllibProviderTokenTransport()
        self._clock = clock
        self._token = ""
        self._expires_at = 0.0

    def token(self) -> str:
        if self._token and self._expires_at - self._clock() > 60:
            return self._token
        url, fields = self._request_fields()
        status, raw = self._transport.request(
            url,
            headers={"content-type": "application/x-www-form-urlencoded"},
            body=urllib.parse.urlencode(fields).encode(),
        )
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("provider workload token endpoint returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise ValueError("provider workload token endpoint returned invalid response")
        access_token = result.get("access_token")
        if status < 200 or status >= 300 or not isinstance(access_token, str) or len(access_token) < 16:
            raise ValueError("provider workload token request was rejected")
        expires_in = result.get("expires_in", 300)
        try:
            expires = max(120, int(expires_in))
        except (TypeError, ValueError) as exc:
            raise ValueError("provider workload token response has invalid expiry") from exc
        self._token = access_token
        self._expires_at = self._clock() + expires
        return access_token

    def _request_fields(self) -> tuple[str, dict[str, str]]:
        if self._identity.mode == "google_service_account":
            return self._google()
        if self._identity.mode == "microsoft_client_certificate":
            return self._microsoft()
        return self._docusign()

    def _google(self) -> tuple[str, dict[str, str]]:
        raw = self._secret()
        try:
            service_account = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Google service account credential must be JSON") from exc
        if not isinstance(service_account, dict):
            raise ValueError("Google service account credential must be an object")
        issuer = _required_string(service_account, "client_email", "Google service account")
        key = _required_string(service_account, "private_key", "Google service account")
        token_url = self._identity.token_url or _required_string(
            service_account, "token_uri", "Google service account"
        )
        subject = self._secret(self._identity.subject_env or "")
        scope = " ".join(self._identity.scopes or ("https://www.googleapis.com/auth/calendar",))
        now = int(self._clock())
        assertion = _signed_jwt(
            {"alg": "RS256", "typ": "JWT"},
            {
                "iss": issuer,
                "sub": subject,
                "aud": token_url,
                "scope": scope,
                "iat": now,
                "exp": now + 3600,
            },
            key,
        )
        return token_url, {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}

    def _microsoft(self) -> tuple[str, dict[str, str]]:
        assert self._identity.client_id is not None and self._identity.tenant_id is not None
        url = self._identity.token_url or (
            f"https://login.microsoftonline.com/{self._identity.tenant_id}/oauth2/v2.0/token"
        )
        now = int(self._clock())
        assertion = _signed_jwt(
            {"alg": "RS256", "typ": "JWT"},
            {
                "aud": url,
                "iss": self._identity.client_id,
                "sub": self._identity.client_id,
                "jti": str(uuid.uuid4()),
                "iat": now,
                "nbf": now,
                "exp": now + 600,
            },
            self._secret(),
        )
        return url, {
            "client_id": self._identity.client_id,
            "grant_type": "client_credentials",
            "scope": " ".join(self._identity.scopes or ("https://graph.microsoft.com/.default",)),
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": assertion,
        }

    def _docusign(self) -> tuple[str, dict[str, str]]:
        assert self._identity.client_id is not None and self._identity.user_id is not None
        url = self._identity.token_url or "https://account-d.docusign.com/oauth/token"
        audience = urllib.parse.urlparse(url).netloc
        now = int(self._clock())
        assertion = _signed_jwt(
            {"alg": "RS256", "typ": "JWT"},
            {
                "iss": self._identity.client_id,
                "sub": self._identity.user_id,
                "aud": audience,
                "scope": " ".join(self._identity.scopes or ("signature", "impersonation")),
                "iat": now,
                "exp": now + 3600,
            },
            self._secret(),
        )
        return url, {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}

    def _secret(self, name: str | None = None) -> str:
        value = self._environment.get(name or self._identity.credential_env, "").strip()
        if not value:
            raise ValueError("provider workload credential is missing")
        return value


def _optional(value: dict[str, Any], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not item:
        raise ValueError(f"{key} must be a non-empty string")
    return item


def _required_string(value: dict[str, Any], key: str, label: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{label} {key} is required")
    return item


def _signed_jwt(header: dict[str, str], claims: dict[str, object], private_key_pem: str) -> str:
    encoded_header = _b64json(header)
    encoded_claims = _b64json(claims)
    signed = f"{encoded_header}.{encoded_claims}".encode()
    try:
        key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    except (TypeError, ValueError) as exc:
        raise ValueError("provider workload private key is invalid") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("provider workload private key must be RSA")
    signature = key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
    return f"{encoded_header}.{encoded_claims}.{_b64(signature)}"


def _b64json(value: dict[str, object]) -> str:
    return _b64(json.dumps(value, separators=(",", ":"), sort_keys=True).encode())


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")
