from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import pytest

from buyer_ops_contracts.connector_runtime import (
    ConnectorAdapterConfig,
    ConnectorRuntimeError,
    HttpsConnectorAdapter,
    PostgresConnectorRuntime,
    configured_adapters_from_environment,
)


def _request(payload: bytes = b"payload") -> dict[str, Any]:
    return {
        "messageType": "connector_request",
        "schemaVersion": "connector-gateway/1.0.0",
        "tenantId": "tenant-1",
        "connectorId": "connector-1",
        "grantId": "grant-1",
        "grantVersion": 1,
        "capability": "read",
        "delegatedPrincipalId": "principal-1",
        "correlationId": "correlation-1",
        "occurredAt": "2026-08-20T12:00:00Z",
        "requestId": "request-1",
        "idempotencyKey": "idempotency-1",
        "payloadDigest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "expectedProviderVersion": None,
    }


class _Response:
    status = 200
    headers = {"x-provider-version": "provider-1"}

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_https_adapter_binds_provider_receipt_and_never_changes_request_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONNECTOR_SECRET", "x" * 32)
    captured: dict[str, Any] = {}

    def urlopen(request: Any, *, timeout: float) -> _Response:
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode())
        return _Response(json.dumps({"receiptId": "provider-receipt-1"}).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    adapter = HttpsConnectorAdapter(
        ConnectorAdapterConfig.from_value(
            {
                "connectorId": "connector-1",
                "endpoint": "https://adapter.example/invoke",
                "secretEnv": "CONNECTOR_SECRET",
                "providerVersion": "configured-1",
            }
        )
    )
    request = _request()
    response = adapter.invoke(request, b"payload")

    assert response["messageType"] == "connector_response"
    assert response["requestId"] == request["requestId"]
    assert response["payloadDigest"] == request["payloadDigest"]
    assert response["receiptId"] == "provider-receipt-1"
    assert captured["body"]["request"] == request
    assert base64.b64decode(captured["body"]["payloadBase64"]) == b"payload"
    assert "x" * 32 not in json.dumps(captured["body"])


def test_https_adapter_rejects_a_provider_response_without_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONNECTOR_SECRET", "x" * 32)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _Response(b"{}"),
    )
    adapter = HttpsConnectorAdapter(
        ConnectorAdapterConfig.from_value(
            {
                "connectorId": "connector-1",
                "endpoint": "https://adapter.example/invoke",
                "secretEnv": "CONNECTOR_SECRET",
            }
        )
    )
    with pytest.raises(ConnectorRuntimeError, match="provider receipt"):
        adapter.invoke(_request(), b"payload")


def test_adapter_configuration_requires_https_and_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        ConnectorAdapterConfig.from_value(
            {
                "connectorId": "connector-1",
                "endpoint": "http://adapter.example/invoke",
                "secretEnv": "CONNECTOR_SECRET",
            }
        )
    monkeypatch.delenv("CONNECTOR_SECRET", raising=False)
    with pytest.raises(ValueError, match="missing or too short"):
        HttpsConnectorAdapter(
            ConnectorAdapterConfig.from_value(
                {
                    "connectorId": "connector-1",
                    "endpoint": "https://adapter.example/invoke",
                    "secretEnv": "CONNECTOR_SECRET",
                }
            )
        )


def test_configured_adapters_are_explicit_and_unique(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONNECTOR_SECRET", "x" * 32)
    monkeypatch.setenv(
        "BUYER_OPS_CONNECTOR_ADAPTERS_JSON",
        json.dumps(
            [
                {
                    "connectorId": "connector-1",
                    "endpoint": "https://adapter.example/invoke",
                    "secretEnv": "CONNECTOR_SECRET",
                }
            ]
        ),
    )
    assert list(configured_adapters_from_environment()) == ["connector-1"]


@pytest.mark.parametrize(
    ("oauth_issuer", "adapter_provider"),
    [("google", "google_calendar"), ("microsoft", "microsoft_graph"), ("docusign", "docusign")],
)
def test_oauth_bound_credentials_map_to_direct_provider_adapters(
    monkeypatch: pytest.MonkeyPatch, oauth_issuer: str, adapter_provider: str
) -> None:
    monkeypatch.setattr(
        "buyer_ops_contracts.connector_runtime.load_connector_credential",
        lambda *args, **kwargs: (
            "calendar-connector",
            oauth_issuer,
            "provider-account",
            "credential-value-with-sufficient-length",
        ),
    )
    runtime = PostgresConnectorRuntime(
        object(),
        tenant_id="tenant-1",
        activation=object(),  # type: ignore[arg-type]
        adapters={},
        permit_secret=b"p" * 32,
    )

    adapter = runtime._adapter_for_request(  # noqa: SLF001
        {"connectorId": "calendar-connector", "grantId": "grant-1"}
    )

    assert adapter is not None
    assert adapter.config.provider == adapter_provider  # type: ignore[attr-defined]


def test_expired_oauth_refresh_uses_database_backed_platform_client_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    monkeypatch.setattr(
        "buyer_ops_contracts.connector_runtime.load_connector_credential",
        lambda *args, **kwargs: None,
    )

    def refresh(*args: Any, **kwargs: Any) -> tuple[str, str, str, str]:
        observed["oauth_clients"] = kwargs["oauth_clients"]
        return (
            "calendar-connector",
            "google",
            "provider-account",
            "credential-value-with-sufficient-length",
        )

    monkeypatch.setattr(
        "buyer_ops_contracts.connector_runtime.refresh_connector_credential", refresh
    )

    class _OAuthStore:
        def __init__(self, connection: Any, *, permit_secret: bytes) -> None:
            del connection, permit_secret

        def material(self) -> dict[str, dict[str, str]]:
            return {"google": {"client_id": "stored-client", "client_secret": "stored-secret"}}

    monkeypatch.setattr("buyer_ops_contracts.connector_runtime.PlatformOAuthStore", _OAuthStore)
    runtime = PostgresConnectorRuntime(
        object(),
        tenant_id="tenant-1",
        activation=object(),  # type: ignore[arg-type]
        adapters={},
        permit_secret=b"p" * 32,
    )

    adapter = runtime._adapter_for_request(  # noqa: SLF001
        {"connectorId": "calendar-connector", "grantId": "grant-1"}
    )

    assert adapter is not None
    assert observed["oauth_clients"]["google"]["client_id"] == "stored-client"
