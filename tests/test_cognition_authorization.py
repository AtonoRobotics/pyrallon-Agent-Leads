from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from buyer_ops_contracts.cognition_authorization import CognitionAuthorization
from buyer_ops_contracts.errors import SetupRejected

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
                "device_code": parameters[3],
                "code_verifier": parameters[4],
                "user_code": parameters[5],
                "expires_at": parameters[8],
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


def _auth(http: _Http, connection: _Connection | None = None) -> CognitionAuthorization:
    return CognitionAuthorization(
        connection or _Connection(),
        tenant_id="1",
        permit_secret=SECRET,
        actor_id="actor-1",
        http=http,
        clock=lambda: NOW,
    )


def test_chatgpt_device_start_fails_before_provider_call_without_identity_admission() -> None:
    http = _Http({"deviceauth/usercode": (200, {})})

    with pytest.raises(SetupRejected) as raised:
        _auth(http).start_chatgpt_device()

    assert raised.value.code == "configuration_incomplete"
    assert raised.value.detail == "OPENAI_CHATGPT_DEVICE_CODE_URL is required"
    assert http.calls == []


def test_claude_subscription_oauth_is_refused() -> None:
    auth = _auth(_Http({}))
    with pytest.raises(SetupRejected, match="anthropic_subscription_oauth_prohibited"):
        auth.refuse_unsupported("claude.subscription")


def test_metered_binding_rejects_a_provider_without_models() -> None:
    http = _Http({"api.openai.com/v1/models": (200, {"data": []})})
    auth = _auth(http)

    with pytest.raises(SetupRejected) as raised:
        auth.bind_metered(connector_id="openai.api", api_key="sk-test-metered-key")

    assert raised.value.code == "provider_rejected"
    assert http.calls == [("GET", "https://api.openai.com/v1/models")]


def test_local_binding_rejects_a_model_not_advertised_by_endpoint() -> None:
    http = _Http({"/models": (200, {"data": []})})
    auth = _auth(http)

    with pytest.raises(SetupRejected) as raised:
        auth.bind_local(base_url="http://model-runtime", model_id="owner-model")

    assert raised.value.code == "provider_rejected"
    assert http.calls == [("GET", "http://model-runtime/models")]


def test_metered_binding_health_checks_and_encrypts_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BUYER_OPS_COGNITIVE_ACTION_CLASSES", "qualification,acknowledgment")
    monkeypatch.setenv("BUYER_OPS_COGNITIVE_CONCURRENCY", "2")
    monkeypatch.setenv("BUYER_OPS_COGNITIVE_DATA_POLICY_VERSION", "data-policy-1")
    http = _Http({"api.openai.com/v1/models": (200, {"data": [{"id": "gpt-4o-mini"}]})})
    connection = _Connection()
    result = _auth(http, connection).bind_metered(
        connector_id="openai.api", api_key="sk-test-metered-key"
    )
    assert result["providerId"] == "openai"
    assert result["state"] == "active"
    assert connection.store["credentials"]
    stored = next(iter(connection.store["credentials"].values()))
    assert stored["identity"]["allowedActionClasses"] == ["qualification", "acknowledgment"]
    assert stored["identity"]["allowedModelFamilies"] == ["gpt-4o-mini"]


def test_local_binding_health_checks_selected_model_and_encrypts_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BUYER_OPS_COGNITIVE_ACTION_CLASSES", "qualification")
    monkeypatch.setenv("BUYER_OPS_COGNITIVE_CONCURRENCY", "1")
    monkeypatch.setenv("BUYER_OPS_COGNITIVE_DATA_POLICY_VERSION", "data-policy-1")
    http = _Http({"/models": (200, {"data": [{"id": "local-model"}]})})
    connection = _Connection()
    result = _auth(http, connection).bind_local(
        base_url="http://model-runtime", model_id="local-model", token="local-secret"
    )
    assert result["authClass"] == "local_endpoint"
    assert result["providerAccountRef"] == "http://model-runtime"
    assert connection.store["credentials"]


def test_chatgpt_device_flow_persists_session_then_binds_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_CHATGPT_DEVICE_CODE_URL", "https://auth.example/device")
    monkeypatch.setenv("OPENAI_CHATGPT_DEVICE_TOKEN_URL", "https://auth.example/token")
    monkeypatch.setenv("BUYER_OPS_COGNITIVE_ACTION_CLASSES", "qualification")
    monkeypatch.setenv("BUYER_OPS_COGNITIVE_CONCURRENCY", "1")
    monkeypatch.setenv("BUYER_OPS_COGNITIVE_DATA_POLICY_VERSION", "data-policy-1")
    http = _Http(
        {
            "auth.example/device": (
                200,
                {
                    "device_auth_id": "device-1",
                    "user_code": "ABCD-EFGH",
                    "verification_url": "https://auth.example/verify",
                    "expires_in": 600,
                    "interval": 5,
                },
            ),
            "auth.example/token": (
                200,
                {"access_token": "subscription-secret", "account_id": "acct-1"},
            ),
        }
    )
    connection = _Connection()
    auth = _auth(http, connection)
    started = auth.start_chatgpt_device()
    assert started["userCode"] == "ABCD-EFGH"
    bound = auth.poll_chatgpt_device(str(started["sessionId"]))
    assert bound["state"] == "bound"
    assert bound["providerId"] == "openai"
    assert connection.store["credentials"]
