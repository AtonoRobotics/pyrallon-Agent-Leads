from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from buyer_ops_contracts.connector_authorization import (
    ConnectorAuthorization,
    PlatformOAuthStore,
    _pkce_challenge,
    _pkce_verifier,
    _platform_key,
    _provider_for_connector,
    _return_origin_allowed,
    canonical_connector_redirect,
    load_connector_credential,
    oauth_clients_from_env,
    parse_oauth_state,
    refresh_connector_credential,
)
from buyer_ops_contracts.errors import SetupRejected

NOW = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)
SECRET = b"x" * 32


class _CredentialCursor:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self.row = row

    def __enter__(self) -> _CredentialCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> None:
        del statement, parameters

    def fetchone(self) -> tuple[object, ...] | None:
        return self.row


class _CredentialConnection:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self.cursor_instance = _CredentialCursor(row)

    def cursor(self) -> _CredentialCursor:
        return self.cursor_instance


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
        elif "FROM canonical_records_current" in statement:
            self._many = [
                (grant,)
                for grant in self.store.get("grants", [])
                if grant.get("connectorId") == parameters[1]
            ]
        elif "UPDATE connector_oauth_sessions" in statement:
            session = self.store["sessions"].get(str(parameters[2]))
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


def test_docusign_oauth_provider_is_first_class_and_uses_signature_scopes() -> None:
    provider = _provider_for_connector(
        "esign.docusign",
        {"docusign": {"client_id": "integration-key", "client_secret": "secret"}},
    )

    assert provider.issuer == "docusign"
    assert provider.scopes == ("signature", "impersonation")
    assert provider.authorize_url.endswith("/oauth/auth")
    assert provider.token_url.endswith("/oauth/token")


def test_docusign_redirect_requires_https_or_localhost() -> None:
    with pytest.raises(SetupRejected, match="http OAuth redirects"):
        canonical_connector_redirect("docusign", "http://192.168.1.10/callback")


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


def test_start_oauth_requires_a_current_actor_bound_grant() -> None:
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
            connector_id="google.workspace",
            redirect_uri="http://127.0.0.1/api/connectors/callback",
        )

    assert raised.value.code == "authority_denied"
    assert connection.store == {"sessions": {}, "credentials": {}}
    assert "canonical_records_current" in connection.cursor_instance.sql
    assert connection.commits == 1
    assert http.calls == []


def test_start_oauth_persists_pkce_session_and_returns_provider_url() -> None:
    http = _Http({})
    connection = _Connection()
    connection.store["grants"] = [
        {
            "id": "grant-google",
            "connectorId": "google.workspace",
            "grantState": "pending",
            "grantorId": "actor-1",
        }
    ]
    auth = ConnectorAuthorization(
        connection,
        tenant_id="brokerage-live-1",
        permit_secret=SECRET,
        oauth_clients={"google": {"client_id": "google-client", "client_secret": "google-secret"}},
        http=http,
        clock=lambda: NOW,
    )
    result = auth.start_oauth(
        actor_id="actor-1",
        connector_id="google.workspace",
        redirect_uri="http://127.0.0.1/api/connectors/callback",
        return_origin="http://127.0.0.1:8090",
    )
    assert result["connectorId"] == "google.workspace"
    assert "accounts.google.com/o/oauth2/v2/auth" in result["authorizationUrl"]
    assert "code_challenge_method=S256" in result["authorizationUrl"]
    assert len(connection.store["sessions"]) == 1
    assert connection.commits == 2


def test_complete_oauth_exchanges_code_and_encrypts_credential() -> None:
    http = _Http(
        {
            "oauth2.googleapis.com/token": (
                200,
                {"access_token": "provider-secret", "expires_in": 3600},
            ),
            "openidconnect.googleapis.com": (200, {"email": "agent@example.com"}),
        }
    )
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

    result = auth.complete_oauth(code="provider-code", state=state, actor_id="actor-1")
    assert result["providerAccountRef"] == "agent@example.com"
    assert result["authorization"] == "bound"
    assert connection.store["sessions"]["session-existing"]["consumed_at"] == NOW
    assert connection.store["credentials"]["grant-existing"]["account"] == "agent@example.com"
    assert connection.store["credentials"]["grant-existing"]["status"] == "bound"
    assert http.calls == [
        ("POST", "https://oauth2.googleapis.com/token"),
        ("GET", "https://openidconnect.googleapis.com/v1/userinfo"),
    ]


def test_docusign_identity_resolves_account_id_from_userinfo_accounts() -> None:
    http = _Http(
        {
            "account-d.docusign.com/oauth/userinfo": (
                200,
                {
                    "accounts": [
                        {"account_id": "docusign-account-1", "base_uri": "https://www.docusign.net"}
                    ]
                },
            )
        }
    )
    auth = ConnectorAuthorization(
        _Connection(),
        tenant_id="brokerage-live-1",
        permit_secret=SECRET,
        oauth_clients={
            "docusign": {
                "client_id": "docusign-client",
                "client_secret": "docusign-secret",
            }
        },
        http=http,
        clock=lambda: NOW,
    )

    provider = _provider_for_connector(
        "esign.docusign",
        {"docusign": {"client_id": "docusign-client", "client_secret": "docusign-secret"}},
    )
    assert auth._provider_identity(provider, "access-token", {}) == "docusign-account-1"


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


def test_load_connector_credential_decrypts_only_the_bound_tenant_grant() -> None:
    nonce = b"0123456789ab"
    ciphertext = AESGCM(_platform_key(SECRET)).encrypt(
        nonce, b"provider-access-token", b"calendar-google"
    )
    row = (
        "calendar-google",
        "google_calendar",
        "google-account-1",
        ciphertext,
        nonce,
        NOW + timedelta(hours=1),
        "bound",
    )

    credential = load_connector_credential(
        _CredentialConnection(row),
        tenant_id="tenant-1",
        grant_id="grant-1",
        connector_id="calendar-google",
        permit_secret=SECRET,
        now=NOW,
    )

    assert credential == (
        "calendar-google",
        "google_calendar",
        "google-account-1",
        "provider-access-token",
    )


class _RefreshCursor:
    def __init__(self, row: tuple[object, ...]) -> None:
        self.row = row
        self.updated: tuple[object, ...] | None = None

    def __enter__(self) -> _RefreshCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> None:
        if "UPDATE connector_credentials" in statement:
            self.updated = parameters

    def fetchone(self) -> tuple[object, ...]:
        return self.row


class _RefreshConnection:
    def __init__(self, row: tuple[object, ...]) -> None:
        self.cursor_instance = _RefreshCursor(row)
        self.commits = 0

    def cursor(self) -> _RefreshCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1


class _RefreshHttp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bytes | None]] = []

    def request(
        self, method: str, url: str, *, headers: dict[str, str], data: bytes | None, timeout: float
    ):
        del headers, timeout
        self.calls.append((method, url, data))
        return 200, {"access_token": "refreshed-access", "expires_in": 3600}


def test_expired_google_oauth_credential_refreshes_without_exposing_refresh_material() -> None:
    connector_id = "google.workspace.calendar"
    refresh_nonce = b"refreshnonce1"
    refresh_ciphertext = AESGCM(_platform_key(SECRET)).encrypt(
        refresh_nonce, b"provider-refresh-token", connector_id.encode()
    )
    connection = _RefreshConnection(
        (connector_id, "google", "google-account-1", refresh_ciphertext, refresh_nonce, "bound")
    )
    http = _RefreshHttp()

    credential = refresh_connector_credential(
        connection,
        tenant_id="tenant-1",
        grant_id="grant-1",
        connector_id=connector_id,
        permit_secret=SECRET,
        now=NOW,
        oauth_clients={"google": {"client_id": "client", "client_secret": "secret"}},
        http=http,
    )

    assert credential == (connector_id, "google", "google-account-1", "refreshed-access")
    assert connection.commits == 1
    assert http.calls[0][0] == "POST"
    assert http.calls[0][1] == "https://oauth2.googleapis.com/token"
    assert b"grant_type=refresh_token" in (http.calls[0][2] or b"")
    assert connection.cursor_instance.updated is not None
    assert b"provider-refresh-token" not in connection.cursor_instance.updated


@pytest.mark.parametrize("status", ["revoked", "expired"])
def test_load_connector_credential_fails_closed_for_inactive_or_expired_binding(
    status: str,
) -> None:
    nonce = b"0123456789ab"
    ciphertext = AESGCM(_platform_key(SECRET)).encrypt(
        nonce, b"provider-access-token", b"calendar-google"
    )
    row = (
        "calendar-google",
        "google_calendar",
        "google-account-1",
        ciphertext,
        nonce,
        NOW - timedelta(hours=1) if status == "expired" else NOW + timedelta(hours=1),
        "bound" if status == "expired" else "revoked",
    )

    assert (
        load_connector_credential(
            _CredentialConnection(row),
            tenant_id="tenant-1",
            grant_id="grant-1",
            connector_id="calendar-google",
            permit_secret=SECRET,
            now=NOW,
        )
        is None
    )
