from __future__ import annotations

import base64
import json
from collections.abc import Mapping

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from buyer_ops_contracts.workload_provider_credentials import (
    ProviderWorkloadCredential,
    ProviderWorkloadIdentity,
)


class TokenTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, str], bytes]] = []

    def request(self, url: str, *, headers: Mapping[str, str], body: bytes) -> tuple[int, bytes]:
        self.calls.append((url, headers, body))
        return 200, b'{"access_token":"renewable-provider-token-1234567890","expires_in":3600}'


def _private_key() -> str:
    return (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        .decode()
    )


def _claims(assertion: str) -> dict[str, object]:
    payload = assertion.split(".")[1] + "==="
    return json.loads(base64.urlsafe_b64decode(payload))


def test_google_workspace_domain_delegation_mints_subject_bound_calendar_token() -> None:
    transport = TokenTransport()
    identity = ProviderWorkloadIdentity.from_value(
        {
            "provider": "google_calendar",
            "credentialMode": "google_service_account",
            "credentialEnv": "GOOGLE_SERVICE_ACCOUNT_JSON",
            "subjectEnv": "GOOGLE_CALENDAR_SUBJECT",
        }
    )
    environment = {
        "GOOGLE_SERVICE_ACCOUNT_JSON": json.dumps(
            {
                "client_email": "calendar-agent@example.iam.gserviceaccount.com",
                "private_key": _private_key(),
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        ),
        "GOOGLE_CALENDAR_SUBJECT": "agent@example.com",
    }

    token = ProviderWorkloadCredential(identity, environment=environment, transport=transport).token()

    assert token == "renewable-provider-token-1234567890"
    url, headers, body = transport.calls[0]
    assert url == "https://oauth2.googleapis.com/token"
    assert headers["content-type"] == "application/x-www-form-urlencoded"
    assertion = body.decode().split("assertion=", 1)[1]
    claims = _claims(assertion)
    assert claims["sub"] == "agent@example.com"
    assert claims["scope"] == "https://www.googleapis.com/auth/calendar"


def test_microsoft_certificate_credential_mints_graph_app_only_token() -> None:
    transport = TokenTransport()
    identity = ProviderWorkloadIdentity.from_value(
        {
            "provider": "microsoft_graph",
            "credentialMode": "microsoft_client_certificate",
            "credentialEnv": "MICROSOFT_CLIENT_CERTIFICATE",
            "clientId": "application-id",
            "tenantId": "tenant-id",
        }
    )
    token = ProviderWorkloadCredential(
        identity, environment={"MICROSOFT_CLIENT_CERTIFICATE": _private_key()}, transport=transport
    ).token()

    assert token == "renewable-provider-token-1234567890"
    url, _, body = transport.calls[0]
    assert url == "https://login.microsoftonline.com/tenant-id/oauth2/v2.0/token"
    fields = dict(item.split("=", 1) for item in body.decode().split("&"))
    assert fields["grant_type"] == "client_credentials"
    assert fields["scope"] == "https%3A%2F%2Fgraph.microsoft.com%2F.default"
    assert _claims(fields["client_assertion"])["sub"] == "application-id"


def test_docusign_jwt_mints_impersonation_token() -> None:
    transport = TokenTransport()
    identity = ProviderWorkloadIdentity.from_value(
        {
            "provider": "docusign",
            "credentialMode": "docusign_jwt",
            "credentialEnv": "DOCUSIGN_RSA_KEY",
            "clientId": "integration-key",
            "userId": "user-guid",
            "tokenUrl": "https://account-d.docusign.com/oauth/token",
        }
    )
    token = ProviderWorkloadCredential(
        identity, environment={"DOCUSIGN_RSA_KEY": _private_key()}, transport=transport
    ).token()

    assert token == "renewable-provider-token-1234567890"
    url, _, body = transport.calls[0]
    assert url == "https://account-d.docusign.com/oauth/token"
    fields = dict(item.split("=", 1) for item in body.decode().split("&"))
    assert fields["grant_type"] == "urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer"
    claims = _claims(fields["assertion"])
    assert claims["iss"] == "integration-key"
    assert claims["sub"] == "user-guid"
    assert claims["scope"] == "signature impersonation"


def test_workload_identity_fails_closed_without_required_subject() -> None:
    with pytest.raises(ValueError, match="subjectEnv"):
        ProviderWorkloadIdentity.from_value(
            {
                "provider": "google_calendar",
                "credentialMode": "google_service_account",
                "credentialEnv": "GOOGLE_SERVICE_ACCOUNT_JSON",
            }
        )
