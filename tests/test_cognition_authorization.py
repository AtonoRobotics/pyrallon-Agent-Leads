from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from buyer_ops_contracts.cognition_authorization import CognitionAuthorization
from buyer_ops_contracts.structural import validate_record
from buyer_ops_contracts.tenant_setup import SetupRejected

NOW = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)
SECRET = b"x" * 32


class _Cursor:
    def __init__(self, store: dict[str, Any]) -> None:
        self.store = store
        self._one: tuple[object, ...] | None = None
        self._many: list[tuple[object, ...]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> None:
        if "INSERT INTO cognitive_oauth_sessions" in statement:
            self.store["sessions"][str(parameters[1])] = {
                "actor_id": parameters[2],
                "provider_id": parameters[3],
                "device_code": parameters[4],
                "code_verifier": parameters[5],
                "user_code": parameters[6],
                "expires_at": parameters[9],
                "consumed_at": None,
            }
        elif "FROM cognitive_oauth_sessions" in statement:
            session = self.store["sessions"].get(str(parameters[1]))
            if session is None:
                self._one = None
            else:
                self._one = (
                    session["actor_id"],
                    session["device_code"],
                    session["code_verifier"],
                    session["user_code"],
                    session["expires_at"],
                    session["consumed_at"],
                    session["provider_id"],
                )
        elif "UPDATE cognitive_oauth_sessions" in statement:
            session = self.store["sessions"].get(str(parameters[2]))
            if session is not None:
                session["consumed_at"] = NOW
        elif "INSERT INTO cognitive_credentials" in statement:
            self.store["credentials"][str(parameters[1])] = {
                "provider_id": parameters[2],
                "auth_class": parameters[3],
                "billing_class": parameters[4],
                "account": parameters[5],
                "identity": parameters[9],
                "status": "bound",
            }
        elif "FROM cognitive_credentials" in statement:
            self._many = [
                (
                    identity_ref,
                    row["provider_id"],
                    row["auth_class"],
                    row["billing_class"],
                    row["account"],
                    row["status"],
                    row["identity"],
                )
                for identity_ref, row in self.store["credentials"].items()
            ]

    def fetchone(self) -> tuple[object, ...] | None:
        return self._one

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._many


class _Connection:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {"sessions": {}, "credentials": {}}
        self.cursor_instance = _Cursor(self.store)

    def cursor(self) -> _Cursor:
        return self.cursor_instance

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


class _Http:
    def __init__(self, responses: dict[str, tuple[int, Any]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        data: bytes | None,
        timeout: float,
    ) -> tuple[int, Any]:
        del headers, data, timeout
        self.calls.append((method, url))
        for prefix, payload in self.responses.items():
            if prefix in url:
                return payload
        return 500, {"error": "unexpected"}


def _auth(http: _Http) -> CognitionAuthorization:
    return CognitionAuthorization(
        _Connection(),
        tenant_id="1",
        permit_secret=SECRET,
        actor_id="actor-1",
        http=http,
        clock=lambda: NOW,
    )


def test_chatgpt_device_start_returns_provider_verification_url() -> None:
    auth = _auth(
        _Http(
            {
                "deviceauth/usercode": (
                    200,
                    {
                        "device_auth_id": "deviceauth_1",
                        "user_code": "ABCD-EFGH",
                        "interval": "5",
                        "expires_at": "2026-08-19T18:15:00+00:00",
                    },
                )
            }
        )
    )
    started = auth.start_chatgpt_device()
    assert started["userCode"] == "ABCD-EFGH"
    assert started["verificationUri"].startswith("https://auth.openai.com/")
    assert started["connectorId"] == "openai.chatgpt"


def test_chatgpt_device_poll_binds_subscription_identity() -> None:
    http = _Http(
        {
            "deviceauth/usercode": (
                200,
                {
                    "device_auth_id": "deviceauth_1",
                    "user_code": "ABCD-EFGH",
                    "interval": "5",
                    "expires_at": "2026-08-19T18:15:00+00:00",
                },
            ),
            "deviceauth/token": (
                200,
                {
                    "authorization_code": "auth-code-1",
                    "code_verifier": "openai-device-verifier",
                    "code_challenge": "openai-device-challenge",
                    "status": "success",
                },
            ),
            "oauth/token": (
                200,
                {
                    "access_token": "access-1",
                    "refresh_token": "refresh-1",
                    "id_token": "id-1",
                    "expires_in": 3600,
                    "account_id": "acct_chatgpt",
                },
            ),
        }
    )
    auth = _auth(http)
    started = auth.start_chatgpt_device()
    bound = auth.poll_chatgpt_device(str(started["sessionId"]))
    assert bound["status"] == "bound"
    identity = bound["identity"]
    assert identity["authClass"] == "subscription_oauth"
    assert identity["billingClass"] == "subscription"
    validate_record(
        {
            "identityRef": identity["identityRef"],
            "tenantId": "1",
            "providerId": "openai",
            "authClass": "subscription_oauth",
            "billingClass": "subscription",
            "subjectType": "entitled_user",
            "subjectRef": "actor-1",
            "allowedActionClasses": ["lead_qualification"],
            "allowedModelFamilies": ["approved-codex-family"],
            "concurrencyLimit": 1,
            "dataPolicyVersion": "data-policy/unactivated",
            "state": "active",
            "expiresAt": _stamp_plus(),
        },
        "gateway_runtime",
    )


def _stamp_plus() -> str:
    return (NOW + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_claude_subscription_oauth_is_refused() -> None:
    auth = _auth(_Http({}))
    with pytest.raises(SetupRejected, match="anthropic_subscription_oauth_prohibited"):
        auth.refuse_unsupported("claude.subscription")


def test_metered_openai_key_is_not_a_subscription() -> None:
    auth = _auth(_Http({"api.openai.com/v1/models": (200, {"data": []})}))
    bound = auth.bind_metered(connector_id="openai.api", api_key="sk-test-metered-key")
    assert bound["authClass"] == "metered_api"
    assert bound["billingClass"] == "metered"
    assert bound["providerAccountRef"].endswith("key")


def test_xai_device_start_returns_provider_verification_url() -> None:
    auth = _auth(
        _Http(
            {
                "oauth2/device/code": (
                    200,
                    {
                        "device_code": "xai-device-1",
                        "user_code": "WXYZ-1234",
                        "verification_uri": "https://auth.x.ai/device",
                        "interval": 5,
                        "expires_in": 1800,
                    },
                )
            }
        )
    )
    started = auth.start_xai_device()
    assert started["userCode"] == "WXYZ-1234"
    assert started["verificationUri"] == "https://auth.x.ai/device"
    assert started["connectorId"] == "xai.subscription"


def test_xai_device_poll_binds_subscription_identity() -> None:
    http = _Http(
        {
            "oauth2/device/code": (
                200,
                {
                    "device_code": "xai-device-1",
                    "user_code": "WXYZ-1234",
                    "verification_uri": "https://auth.x.ai/device",
                    "interval": 5,
                    "expires_in": 1800,
                },
            ),
            "oauth2/token": (
                200,
                {
                    "access_token": "xai-access-1",
                    "refresh_token": "xai-refresh-1",
                    "expires_in": 3600,
                    "account_id": "acct_grok",
                },
            ),
        }
    )
    auth = _auth(http)
    started = auth.start_xai_device()
    bound = auth.poll_device(str(started["sessionId"]))
    assert bound["status"] == "bound"
    identity = bound["identity"]
    assert identity["authClass"] == "subscription_oauth"
    assert identity["billingClass"] == "subscription"
    assert identity["connectorId"] == "xai.subscription"
    validate_record(
        {
            "identityRef": identity["identityRef"],
            "tenantId": "1",
            "providerId": "xai",
            "authClass": "subscription_oauth",
            "billingClass": "subscription",
            "subjectType": "entitled_user",
            "subjectRef": "actor-1",
            "allowedActionClasses": ["lead_qualification"],
            "allowedModelFamilies": ["approved-xai-family"],
            "concurrencyLimit": 1,
            "dataPolicyVersion": "data-policy/unactivated",
            "state": "active",
            "expiresAt": _stamp_plus(),
        },
        "gateway_runtime",
    )


def test_xai_device_poll_stays_pending_until_authorized() -> None:
    http = _Http(
        {
            "oauth2/device/code": (
                200,
                {
                    "device_code": "xai-device-1",
                    "user_code": "WXYZ-1234",
                    "verification_uri": "https://auth.x.ai/device",
                    "interval": 5,
                    "expires_in": 1800,
                },
            ),
            "oauth2/token": (400, {"error": "authorization_pending"}),
        }
    )
    auth = _auth(http)
    started = auth.start_xai_device()
    polled = auth.poll_device(str(started["sessionId"]))
    assert polled == {"status": "pending", "sessionId": started["sessionId"]}


def test_chatgpt_poll_after_bind_returns_bound_identity() -> None:
    http = _Http(
        {
            "deviceauth/usercode": (
                200,
                {
                    "device_auth_id": "deviceauth_1",
                    "user_code": "ABCD-EFGH",
                    "interval": "5",
                    "expires_at": "2026-08-19T18:15:00+00:00",
                },
            ),
            "deviceauth/token": (
                200,
                {
                    "authorization_code": "auth-code-1",
                    "code_verifier": "openai-device-verifier",
                    "code_challenge": "openai-device-challenge",
                    "status": "success",
                },
            ),
            "oauth/token": (
                200,
                {
                    "access_token": "access-1",
                    "refresh_token": "refresh-1",
                    "id_token": "id-1",
                    "expires_in": 3600,
                    "account_id": "acct_chatgpt",
                },
            ),
        }
    )
    auth = _auth(http)
    started = auth.start_chatgpt_device()
    first = auth.poll_device(str(started["sessionId"]))
    assert first["status"] == "bound"
    second = auth.poll_device(str(started["sessionId"]))
    assert second["status"] == "bound"
    assert second["identity"]["connectorId"] == "openai.chatgpt"


def test_metered_xai_key_is_not_a_subscription() -> None:
    auth = _auth(_Http({"api.x.ai/v1/models": (200, {"data": []})}))
    bound = auth.bind_metered(connector_id="xai.api", api_key="xai-test-metered-key")
    assert bound["authClass"] == "metered_api"
    assert bound["billingClass"] == "metered"
    assert bound["connectorId"] == "xai.api"
