from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from buyer_ops_contracts.connector_authorization import (
    ConnectorAuthorization,
    PlatformOAuthStore,
    _pkce_challenge,
    _pkce_verifier,
    _return_origin_allowed,
    canonical_connector_redirect,
    oauth_clients_from_env,
    parse_oauth_state,
)
from buyer_ops_contracts.errors import SetupRejected

NOW = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)
SECRET = b"x" * 32


class _Cursor:
    def __init__(self, store: dict[str, Any]) -> None:
        self.store = store
        self.params: tuple[object, ...] = ()
        self.sql = ""
        self._one: tuple[object, ...] | None = None
        self._many: list[tuple[object, ...]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> None:
        self.sql = statement
        self.params = parameters
        if "INSERT INTO connector_oauth_sessions" in statement:
            self.store["sessions"][str(parameters[1])] = {
                "actor_id": parameters[2],
                "connector_id": parameters[3],
                "grant_id": parameters[4],
                "redirect_uri": parameters[5],
                "code_verifier": parameters[6],
                "expires_at": parameters[7],
                "return_origin": parameters[8] if len(parameters) > 8 else "",
                "consumed_at": None,
            }
        elif "FROM connector_oauth_sessions" in statement and "FOR UPDATE" in statement:
            session = self.store["sessions"].get(str(parameters[1]))
            if session is None:
                self._one = None
            else:
                self._one = (
                    parameters[1],
                    session["actor_id"],
                    session["connector_id"],
                    session["grant_id"],
                    session["redirect_uri"],
                    session["code_verifier"],
                    session["expires_at"],
                    session["consumed_at"],
                    session.get("return_origin") or "",
                )
        elif "UPDATE connector_oauth_sessions" in statement:
            session = self.store["sessions"].get(str(parameters[1]))
            if session is not None:
                session["consumed_at"] = NOW
        elif "INSERT INTO connector_credentials" in statement:
            self.store["credentials"][str(parameters[1])] = {
                "account": parameters[4],
                "status": "bound",
            }
        elif "FROM connector_credentials" in statement:
            self._many = [
                (grant_id, "google.workspace.email", row["account"], row["status"])
                for grant_id, row in self.store["credentials"].items()
            ]

    def fetchone(self) -> tuple[object, ...] | None:
        return self._one

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._many


class _Connection:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {"sessions": {}, "credentials": {}}
        self.cursor_instance = _Cursor(self.store)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _Cursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

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


def _auth(http: _Http | None = None) -> ConnectorAuthorization:
    return ConnectorAuthorization(
        _Connection(),
        tenant_id="brokerage-live-1",
        permit_secret=SECRET,
        oauth_clients={
            "google": {"client_id": "google-client", "client_secret": "google-secret"},
            "microsoft": {
                "client_id": "ms-client",
                "client_secret": "ms-secret",
                "directory_id": "common",
            },
        },
        http=http or _Http({}),
        clock=lambda: NOW,
    )


def test_oauth_state_round_trip() -> None:
    auth = _auth()
    expires = NOW + timedelta(minutes=10)
    state = auth._sign_state("session-1", expires)
    tenant_id, session_id = parse_oauth_state(SECRET, state, now=NOW)
    assert tenant_id == "brokerage-live-1"
    assert session_id == "session-1"


def test_oauth_state_rejects_tampering() -> None:
    auth = _auth()
    state = auth._sign_state("session-1", NOW + timedelta(minutes=10))
    with pytest.raises(SetupRejected, match="not authentic"):
        parse_oauth_state(SECRET, state + "00", now=NOW)


def test_pkce_uses_s256_without_non_url_safe_characters() -> None:
    verifier = _pkce_verifier()
    assert len(verifier) == 64
    assert verifier.replace("-", "").replace("_", "").isalnum()
    assert (
        _pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_provider_redirect_uses_public_origin_and_rejects_private_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPERATOR_PUBLIC_URL", "https://operator.example.com/")
    assert canonical_connector_redirect("google", "http://127.0.0.1/ignored") == (
        "https://operator.example.com/api/connectors/callback"
    )

    monkeypatch.delenv("OPERATOR_PUBLIC_URL")
    assert canonical_connector_redirect("microsoft", "http://127.0.0.1/callback") == (
        "http://127.0.0.1/callback"
    )
    with pytest.raises(SetupRejected, match="OPERATOR_PUBLIC_URL"):
        canonical_connector_redirect("google", "http://192.168.1.20/callback")


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        ("http://localhost:8180", True),
        ("http://192.168.1.20:8180", True),
        ("https://operator.tailnet.ts.net", True),
        ("https://unconfigured.example.com", False),
        ("javascript:alert(1)", False),
    ],
)
def test_return_origin_boundary(origin: str, allowed: bool) -> None:
    assert _return_origin_allowed(origin) is allowed


def test_platform_oauth_store_accepts_google_and_twilio() -> None:
    store = PlatformOAuthStore(_Connection(), permit_secret=SECRET)
    with pytest.raises(SetupRejected, match="issuer must be"):
        store.save(issuer="imap", client_id="id", client_secret="secret")
    with pytest.raises(SetupRejected, match="Twilio Connect App SID"):
        store.save(issuer="twilio", client_id="", client_secret="")
    with pytest.raises(SetupRejected, match="Microsoft OAuth directory"):
        store.save(issuer="microsoft", client_id="id", client_secret="secret")
    saved = store.save(issuer="twilio", client_id="CNconnectapp", client_secret="")
    assert saved == {"issuer": "twilio", "clientId": "CNconnectapp", "configured": "true"}


def test_microsoft_environment_has_no_implicit_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MICROSOFT_OAUTH_TENANT_ID", raising=False)
    assert oauth_clients_from_env()["microsoft"]["directory_id"] == ""


def test_platform_oauth_readiness_requires_microsoft_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_ID", "env-ms-client")
    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_SECRET", "env-ms-secret")
    monkeypatch.setenv("MICROSOFT_OAUTH_TENANT_ID", "env-tenant")
    connection = _Connection()
    connection.cursor_instance._many = [("microsoft", "legacy-ms-client", None)]
    store = PlatformOAuthStore(connection, permit_secret=SECRET)

    public = {item["issuer"]: item for item in store.list_public()}
    assert public["microsoft"]["configured"] == "false"
    monkeypatch.setattr(
        store,
        "client_for",
        lambda issuer: (
            {
                "client_id": "legacy-ms-client",
                "client_secret": "legacy-ms-secret",
                "directory_id": "   ",
            }
            if issuer == "microsoft"
            else {}
        ),
    )
    assert store.material()["microsoft"] == {}


@pytest.mark.parametrize(
    "connector_id",
    ["google.workspace", "microsoft.365", "twilio.sms"],
)
def test_start_oauth_fails_closed_without_governing_admission(
    connector_id: str,
) -> None:
    http = _Http({})
    connection = _Connection()
    auth = ConnectorAuthorization(
        connection,
        tenant_id="brokerage-live-1",
        permit_secret=SECRET,
        oauth_clients={
            "google": {"client_id": "google-client", "client_secret": "google-secret"},
            "microsoft": {
                "client_id": "ms-client",
                "client_secret": "ms-secret",
                "directory_id": "tenant-id",
            },
            "twilio": {"client_id": "CNconnectapp", "client_secret": ""},
        },
        http=http,
        clock=lambda: NOW,
    )

    with pytest.raises(SetupRejected) as raised:
        auth.start_oauth(
            actor_id="actor-1",
            connector_id=connector_id,
            redirect_uri="http://127.0.0.1/api/connectors/callback",
        )

    assert raised.value.code == "configuration_incomplete"
    assert connection.store == {"sessions": {}, "credentials": {}}
    assert connection.cursor_instance.sql == ""
    assert connection.commits == 0
    assert connection.rollbacks == 0
    assert http.calls == []


def test_complete_oauth_fails_closed_without_consuming_existing_session() -> None:
    http = _Http({})
    connection = _Connection()
    auth = ConnectorAuthorization(
        connection,
        tenant_id="brokerage-live-1",
        permit_secret=SECRET,
        oauth_clients={"google": {"client_id": "google-client", "client_secret": "google-secret"}},
        http=http,
        clock=lambda: NOW,
    )
    expires = NOW + timedelta(minutes=10)
    connection.store["sessions"]["session-existing"] = {
        "actor_id": "actor-1",
        "connector_id": "google.workspace.email",
        "grant_id": "grant-existing",
        "redirect_uri": "http://127.0.0.1/api/connectors/callback",
        "code_verifier": "verifier",
        "expires_at": expires,
        "consumed_at": None,
    }
    state = auth._sign_state("session-existing", expires)

    with pytest.raises(SetupRejected) as raised:
        auth.complete_oauth(code="provider-code", state=state, actor_id="actor-1")

    assert raised.value.code == "configuration_incomplete"
    assert connection.store["sessions"]["session-existing"]["consumed_at"] is None
    assert connection.store["credentials"] == {}
    assert connection.cursor_instance.sql == ""
    assert connection.commits == 0
    assert connection.rollbacks == 0
    assert http.calls == []


def test_existing_connector_binding_remains_readable_without_mutation() -> None:
    connection = _Connection()
    connection.store["credentials"]["grant-existing"] = {
        "account": "agent@example.com",
        "status": "bound",
    }
    http = _Http({})
    auth = ConnectorAuthorization(
        connection,
        tenant_id="brokerage-live-1",
        permit_secret=SECRET,
        oauth_clients={},
        http=http,
        clock=lambda: NOW,
    )

    assert auth.bindings() == {
        "grant-existing": {
            "grant_id": "grant-existing",
            "connector_id": "google.workspace.email",
            "provider_account_ref": "agent@example.com",
            "authorization": "bound",
        }
    }
    assert "SELECT grant_id" in connection.cursor_instance.sql
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert http.calls == []
