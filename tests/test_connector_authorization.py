from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from buyer_ops_contracts.connector_authorization import (
    ConnectorAuthorization,
    PlatformOAuthStore,
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


def test_start_oauth_requires_configured_client() -> None:
    auth = ConnectorAuthorization(
        _Connection(),
        tenant_id="brokerage-live-1",
        permit_secret=SECRET,
        oauth_clients={},
        clock=lambda: NOW,
    )
    with pytest.raises(SetupRejected, match="google_oauth_app_required"):
        auth.start_oauth(
            actor_id="actor-1",
            connector_id="google.workspace.email",
            redirect_uri="http://127.0.0.1:8180/api/connectors/callback",
        )


def test_google_oauth_allows_https_hostname_redirect() -> None:
    auth = _auth()
    auth._require_or_create_grant = lambda connector_id, spec: {  # type: ignore[method-assign]
        "id": f"grant:{connector_id}",
        "grantState": "pending",
    }
    started = auth.start_oauth(
        actor_id="actor-1",
        connector_id="google.workspace",
        redirect_uri="https://strix-alpha.tailfc1d45.ts.net/api/connectors/callback",
        return_origin="http://192.168.0.50:8180",
    )
    assert "accounts.google.com" in started["authorizationUrl"]
    assert "strix-alpha.tailfc1d45.ts.net" in started["authorizationUrl"]


def test_google_oauth_public_url_overrides_loopback_and_lan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPERATOR_PUBLIC_URL", "https://strix-alpha.tailfc1d45.ts.net")
    auth = _auth()
    auth._require_or_create_grant = lambda connector_id, spec: {  # type: ignore[method-assign]
        "id": f"grant:{connector_id}",
        "grantState": "pending",
    }
    started = auth.start_oauth(
        actor_id="actor-1",
        connector_id="google.workspace",
        redirect_uri="http://127.0.0.1:8180/api/connectors/callback",
        return_origin="http://192.168.0.50:8180",
    )
    assert started["redirectUri"] == "https://strix-alpha.tailfc1d45.ts.net/api/connectors/callback"
    assert "127.0.0.1" not in started["authorizationUrl"]
    assert "192.168.0.50" not in started["authorizationUrl"]
    assert "strix-alpha.tailfc1d45.ts.net" in started["authorizationUrl"]
    assert "prompt=consent" not in started["authorizationUrl"]


def test_google_oauth_rejects_private_http_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPERATOR_PUBLIC_URL", raising=False)
    auth = _auth()
    auth._require_or_create_grant = lambda connector_id, spec: {  # type: ignore[method-assign]
        "id": f"grant:{connector_id}",
        "grantState": "pending",
    }
    with pytest.raises(SetupRejected, match="127.0.0.1") as raised:
        auth.start_oauth(
            actor_id="actor-1",
            connector_id="google.workspace",
            redirect_uri="http://192.168.0.50:8180/api/connectors/callback",
        )
    assert "8180" not in raised.value.detail
    assert "OPERATOR_PUBLIC_URL" in raised.value.detail


def test_google_workspace_connect_asks_for_mail_and_calendar() -> None:
    auth = _auth()
    auth._require_or_create_grant = lambda connector_id, spec: {  # type: ignore[method-assign]
        "id": f"grant:{connector_id}",
        "grantState": "pending",
    }
    started = auth.start_oauth(
        actor_id="actor-1",
        connector_id="google.workspace",
        redirect_uri="http://127.0.0.1:8180/api/connectors/callback",
    )
    assert "calendar.events" in started["authorizationUrl"]
    assert "gmail.send" in started["authorizationUrl"]
    assert started["connectorId"] == "google.workspace"
    assert "prompt=consent" not in started["authorizationUrl"]
    assert "include_granted_scopes=true" in started["authorizationUrl"]


def test_start_oauth_refuses_unknown_connector() -> None:
    with pytest.raises(SetupRejected, match="does not use OAuth"):
        _auth().start_oauth(
            actor_id="actor-1",
            connector_id="imap.password",
            redirect_uri="http://127.0.0.1:8180/api/connectors/callback",
        )


def test_connect_twilio_redirects_to_twilio() -> None:
    auth = ConnectorAuthorization(
        _Connection(),
        tenant_id="brokerage-live-1",
        permit_secret=SECRET,
        oauth_clients={"twilio": {"client_id": "CNconnectapp", "client_secret": ""}},
        clock=lambda: NOW,
    )
    auth._require_or_create_grant = lambda connector_id, spec: {  # type: ignore[method-assign]
        "id": "grant:twilio.sms:brokerage-live-1",
        "grantState": "pending",
    }
    started = auth.start_oauth(
        actor_id="actor-1",
        connector_id="twilio.sms",
        redirect_uri="http://127.0.0.1:8180/api/connectors/callback",
    )
    assert started["authorizationUrl"].startswith("https://www.twilio.com/authorize/CNconnectapp")


def _ready_google_completion(
    token_payload: dict[str, Any],
) -> tuple[ConnectorAuthorization, _Http, str]:
    http = _Http(
        {
            "oauth2.googleapis.com/token": (200, token_payload),
            "googleapis.com/oauth2/v2/userinfo": (200, {"email": "agent@atonobrokerage.com"}),
        }
    )
    auth = _auth(http)
    expires = NOW + timedelta(minutes=10)
    session_id = "sess_live"
    auth._connection.store["sessions"][session_id] = {  # type: ignore[attr-defined]
        "actor_id": "actor-1",
        "connector_id": "google.workspace.email",
        "grant_id": "grant:google.workspace.email:brokerage-live-1",
        "redirect_uri": "http://127.0.0.1:8180/api/connectors/callback",
        "code_verifier": "verifier",
        "expires_at": expires,
        "consumed_at": None,
    }
    state = auth._sign_state(session_id, expires)
    auth._client_for = lambda issuer: {  # type: ignore[method-assign]
        "client_id": "google-client",
        "client_secret": "google-secret",
    }
    auth._require_or_create_grant = lambda connector_id, spec: {  # type: ignore[method-assign]
        "id": f"grant:{connector_id}:brokerage-live-1",
        "grantState": "pending",
    }
    auth._activate_grant = lambda grant_id, account: {  # type: ignore[method-assign]
        "id": grant_id,
        "grantState": "active",
        "grantId": grant_id,
    }
    return auth, http, state


def test_complete_oauth_exchanges_code_and_binds_account() -> None:
    auth, http, state = _ready_google_completion(
        {
            "access_token": "ya29.access",
            "refresh_token": "1//refresh",
            "expires_in": 3600,
            "scope": (
                "https://www.googleapis.com/auth/gmail.send "
                "https://www.googleapis.com/auth/gmail.readonly "
                "https://www.googleapis.com/auth/userinfo.email"
            ),
            "token_type": "Bearer",
        }
    )
    result = auth.complete_oauth(code="4/real-code", state=state, actor_id="actor-1")
    assert result["authorization"] == "bound"
    assert result["providerAccountRef"] == "agent@atonobrokerage.com"
    assert result["grantState"] == "active"
    assert http.calls[0][0] == "POST"
    assert "oauth2.googleapis.com/token" in http.calls[0][1]
    assert "userinfo" in http.calls[1][1]
    assert "grant:google.workspace.email:brokerage-live-1" in auth._connection.store["credentials"]  # type: ignore[attr-defined]


@pytest.mark.parametrize("expires_in", [None, 0, -1, True, "invalid", 10**100])
def test_complete_oauth_requires_provider_observed_positive_expiry(expires_in: Any) -> None:
    auth, _, state = _ready_google_completion(
        {
            "access_token": "ya29.access",
            "expires_in": expires_in,
            "scope": (
                "https://www.googleapis.com/auth/gmail.send "
                "https://www.googleapis.com/auth/gmail.readonly "
                "https://www.googleapis.com/auth/userinfo.email"
            ),
        }
    )

    with pytest.raises(SetupRejected, match="token expiry"):
        auth.complete_oauth(code="4/real-code", state=state, actor_id="actor-1")
    assert auth._connection.store["credentials"] == {}  # type: ignore[attr-defined]


@pytest.mark.parametrize("scope", [None, "", "https://www.googleapis.com/auth/gmail.send"])
def test_complete_oauth_requires_explicit_complete_provider_scope(scope: str | None) -> None:
    auth, _, state = _ready_google_completion(
        {"access_token": "ya29.access", "expires_in": 3600, "scope": scope}
    )

    with pytest.raises(SetupRejected, match="granted scope"):
        auth.complete_oauth(code="4/real-code", state=state, actor_id="actor-1")
    assert auth._connection.store["credentials"] == {}  # type: ignore[attr-defined]


def test_microsoft_oauth_requires_explicit_directory_authority() -> None:
    auth = ConnectorAuthorization(
        _Connection(),
        tenant_id="brokerage-live-1",
        permit_secret=SECRET,
        oauth_clients={"microsoft": {"client_id": "ms-client", "client_secret": "ms-secret"}},
        clock=lambda: NOW,
    )
    auth._require_or_create_grant = lambda connector_id, spec: {  # type: ignore[method-assign]
        "id": f"grant:{connector_id}",
        "grantState": "pending",
    }

    with pytest.raises(SetupRejected, match="microsoft_oauth_directory_required"):
        auth.start_oauth(
            actor_id="actor-1",
            connector_id="microsoft.365.email",
            redirect_uri="http://127.0.0.1:8180/api/connectors/callback",
        )


def test_stored_microsoft_oauth_client_requires_directory_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        PlatformOAuthStore,
        "client_for",
        lambda self, issuer: {
            "client_id": "stored-ms-client",
            "client_secret": "stored-ms-secret",
            "directory_id": "",
        },
    )
    auth = _auth()
    auth._require_or_create_grant = lambda connector_id, spec: {  # type: ignore[method-assign]
        "id": f"grant:{connector_id}",
        "grantState": "pending",
    }

    with pytest.raises(SetupRejected, match="microsoft_oauth_directory_required"):
        auth.start_oauth(
            actor_id="actor-1",
            connector_id="microsoft.365.email",
            redirect_uri="http://127.0.0.1:8180/api/connectors/callback",
        )


def test_microsoft_directory_is_trimmed_and_url_path_encoded() -> None:
    auth = ConnectorAuthorization(
        _Connection(),
        tenant_id="brokerage-live-1",
        permit_secret=SECRET,
        oauth_clients={
            "microsoft": {
                "client_id": "ms-client",
                "client_secret": "ms-secret",
                "directory_id": " tenant/path?query#fragment ",
            }
        },
        clock=lambda: NOW,
    )
    auth._require_or_create_grant = lambda connector_id, spec: {  # type: ignore[method-assign]
        "id": f"grant:{connector_id}",
        "grantState": "pending",
    }

    started = auth.start_oauth(
        actor_id="actor-1",
        connector_id="microsoft.365.email",
        redirect_uri="http://127.0.0.1:8180/api/connectors/callback",
    )
    assert "/tenant%2Fpath%3Fquery%23fragment/oauth2/" in started["authorizationUrl"]


def test_complete_oauth_refuses_replayed_session() -> None:
    auth = _auth()
    expires = NOW + timedelta(minutes=10)
    session_id = "sess_replay"
    auth._connection.store["sessions"][session_id] = {  # type: ignore[attr-defined]
        "actor_id": "actor-1",
        "connector_id": "google.workspace.email",
        "grant_id": "grant-1",
        "redirect_uri": "http://127.0.0.1:8180/api/connectors/callback",
        "code_verifier": "verifier",
        "expires_at": expires,
        "consumed_at": NOW,
    }
    state = auth._sign_state(session_id, expires)
    with pytest.raises(SetupRejected, match="already consumed"):
        auth.complete_oauth(code="4/code", state=state, actor_id="actor-1")
