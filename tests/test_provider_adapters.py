import json
from collections.abc import Mapping

import pytest

from buyer_ops_contracts.provider_adapters import (
    DirectProviderAdapter,
    DirectProviderConfig,
    ProviderAdapterError,
)


class Transport:
    def __init__(self, response: dict, status: int = 200) -> None:
        self.response = response
        self.status = status
        self.calls: list[tuple[str, str, Mapping[str, str], bytes | None]] = []

    def request(self, method, url, *, headers, body, timeout):
        self.calls.append((method, url, headers, body))
        return (
            self.status,
            {"x-api-version": "test-v1", "x-message-id": "provider-message-1"},
            json.dumps(self.response).encode(),
        )


def _request(connector: str) -> dict:
    return {
        "tenantId": "tenant-1",
        "connectorId": connector,
        "grantId": "grant-1",
        "grantVersion": 1,
        "capability": "calendar",
        "delegatedPrincipalId": "principal-1",
        "correlationId": "correlation-1",
        "requestId": "request-1",
        "idempotencyKey": "idempotency-1",
        "payloadDigest": "sha256:" + "a" * 64,
    }


def test_google_calendar_adapter_binds_idempotency_and_bearer_without_leaking_credential() -> None:
    transport = Transport(
        {"id": "event-1", "status": "confirmed", "authorization": "do-not-return"}
    )
    adapter = DirectProviderAdapter(
        DirectProviderConfig("calendar-google", "google_calendar", "TOKEN"),
        transport=transport,
        credential="x" * 32,
    )

    result = adapter.invoke(
        _request("calendar-google"),
        json.dumps(
            {
                "action": "calendar.book",
                "calendarId": "primary",
                "summary": "Consult",
                "start": "2026-03-08T09:00:00Z",
                "end": "2026-03-08T09:30:00Z",
            }
        ).encode(),
    )

    assert result["outcome"] == "confirmed"
    assert result["receiptId"] == "event-1"
    assert result["providerResponse"]["id"] == "event-1"
    assert "authorization" not in result
    method, url, headers, body = transport.calls[0]
    assert method == "POST"
    assert url.endswith("/calendars/primary/events")
    assert headers["Idempotency-Key"] == "idempotency-1"
    assert headers["Authorization"] == "Bearer " + "x" * 32
    assert body is not None
    event = json.loads(body)
    assert event["start"] == {"dateTime": "2026-03-08T09:00:00Z", "timeZone": "UTC"}
    assert event["end"] == {"dateTime": "2026-03-08T09:30:00Z", "timeZone": "UTC"}


def test_google_calendar_reschedule_updates_existing_event():
    transport = Transport({"id": "event-1", "status": "confirmed"})
    adapter = DirectProviderAdapter(
        DirectProviderConfig("calendar-google", "google_calendar", "TOKEN"),
        transport=transport,
        credential="x" * 32,
    )

    adapter.invoke(
        _request("calendar-google"),
        json.dumps(
            {
                "action": "calendar.reschedule",
                "calendarId": "primary",
                "id": "event-1",
                "start": "2026-03-08T10:00:00Z",
                "end": "2026-03-08T10:30:00Z",
            }
        ).encode(),
    )

    method, url, _, _ = transport.calls[0]
    assert method == "PATCH"
    assert url.endswith("/calendars/primary/events/event-1")


def test_microsoft_calendar_reschedule_updates_existing_event():
    transport = Transport({"id": "event-1", "status": "confirmed"})
    adapter = DirectProviderAdapter(
        DirectProviderConfig("calendar-ms", "microsoft_graph", "TOKEN"),
        transport=transport,
        credential="x" * 32,
    )

    adapter.invoke(
        _request("calendar-ms"),
        json.dumps(
            {
                "action": "calendar.reschedule",
                "accountId": "me",
                "id": "event-1",
                "start": "2026-03-08T10:00:00Z",
                "end": "2026-03-08T10:30:00Z",
            }
        ).encode(),
    )

    method, url, _, body = transport.calls[0]
    assert method == "PATCH"
    assert url.endswith("/users/me/events/event-1")
    assert body is not None
    event = json.loads(body)
    assert event["subject"] == "Buyer consultation"
    assert event["start"]["timeZone"] == "UTC"


def test_google_calendar_availability_keeps_unescaped_calendar_id_in_freebusy_body():
    transport = Transport({"calendars": {"team/calendar": {"busy": []}}})
    adapter = DirectProviderAdapter(
        DirectProviderConfig("calendar-google", "google_calendar", "TOKEN"),
        transport=transport,
        credential="x" * 32,
    )

    adapter.invoke(
        _request("calendar-google"),
        json.dumps(
            {
                "action": "calendar.availability",
                "calendarId": "team/calendar",
                "timeMin": "2026-03-08T08:00:00Z",
                "timeMax": "2026-03-08T18:00:00Z",
            }
        ).encode(),
    )

    body = transport.calls[0][3]
    assert body is not None
    assert json.loads(body)["items"] == [{"id": "team/calendar"}]


def test_microsoft_calendar_availability_uses_graph_schedule_shape():
    transport = Transport({"value": []})
    adapter = DirectProviderAdapter(
        DirectProviderConfig("calendar-ms", "microsoft_graph", "TOKEN", account_id="me"),
        transport=transport,
        credential="x" * 32,
    )

    adapter.invoke(
        _request("calendar-ms"),
        json.dumps(
            {
                "action": "calendar.availability",
                "calendarId": "me",
                "timeMin": "2026-03-08T08:00:00Z",
                "timeMax": "2026-03-08T18:00:00Z",
            }
        ).encode(),
    )

    body = transport.calls[0][3]
    assert body is not None
    assert json.loads(body)["schedules"] == ["me"]
    assert json.loads(body)["startTime"]["dateTime"] == "2026-03-08T08:00:00Z"


def test_sendgrid_adapter_translates_message_to_mail_send_shape():
    transport = Transport({"messageId": "message-1"})
    adapter = DirectProviderAdapter(
        DirectProviderConfig("email-sendgrid", "sendgrid", "TOKEN"),
        transport=transport,
        credential="x" * 32,
    )

    result = adapter.invoke(
        _request("email-sendgrid"),
        json.dumps(
            {
                "action": "email.send",
                "to": "buyer@example.test",
                "from": "agent@example.test",
                "subject": "Consultation",
                "text": "Available times",
            }
        ).encode(),
    )

    assert result["receiptId"] == "message-1"
    body = transport.calls[0][3]
    assert body is not None
    assert json.loads(body)["personalizations"] == [{"to": [{"email": "buyer@example.test"}]}]


def test_docusign_void_is_an_explicit_envelope_mutation():
    transport = Transport({"envelopeId": "envelope-1", "status": "voided"})
    adapter = DirectProviderAdapter(
        DirectProviderConfig("esign-docusign", "docusign", "TOKEN", account_id="account-1"),
        transport=transport,
        credential="x" * 32,
    )

    result = adapter.invoke(
        _request("esign-docusign"),
        json.dumps(
            {"action": "esign.void", "id": "envelope-1", "voidedReason": "approval-revoked"}
        ).encode(),
    )

    assert result["receiptId"] == "envelope-1"
    method, url, _, body = transport.calls[0]
    assert method == "PUT"
    assert url.endswith("/accounts/account-1/envelopes/envelope-1")
    assert body is not None and json.loads(body) == {
        "status": "voided",
        "voidedReason": "approval-revoked",
    }


def test_docusign_create_translates_governed_request_to_template_envelope():
    transport = Transport({"envelopeId": "envelope-2", "status": "sent"})
    adapter = DirectProviderAdapter(
        DirectProviderConfig("esign-docusign", "docusign", "TOKEN", account_id="account-1"),
        transport=transport,
        credential="x" * 32,
    )

    result = adapter.invoke(
        _request("esign-docusign"),
        json.dumps(
            {
                "action": "esign.create",
                "templateId": "template-1",
                "agreementDigest": "sha256:" + "a" * 64,
                "recipients": [
                    {"roleName": "Buyer", "name": "Buyer One", "email": "buyer@example.test"}
                ],
            }
        ).encode(),
    )

    assert result["receiptId"] == "envelope-2"
    body = transport.calls[0][3]
    assert body is not None
    assert json.loads(body) == {
        "status": "sent",
        "templateId": "template-1",
        "emailSubject": "Buyer representation agreement",
        "customFields": {
            "textCustomFields": [{"name": "buyerOpsAgreementDigest", "value": "sha256:" + "a" * 64}]
        },
        "templateRoles": [
            {"roleName": "Buyer", "name": "Buyer One", "email": "buyer@example.test"}
        ],
    }


def test_twilio_adapter_uses_basic_auth_and_never_returns_auth_token() -> None:
    transport = Transport({"sid": "SM-1", "body": "sent", "auth_token": "secret"})
    adapter = DirectProviderAdapter(
        DirectProviderConfig("sms-twilio", "twilio", "TOKEN"),
        transport=transport,
        credential="AC123456789012345:auth-token-123456789",
    )

    result = adapter.invoke(
        _request("sms-twilio"),
        json.dumps(
            {"action": "sms.send", "to": "+15125550100", "from": "+15125550101", "text": "Hello"}
        ).encode(),
    )

    assert result["receiptId"] == "SM-1"
    assert "auth_token" not in result
    assert transport.calls[0][2]["Authorization"].startswith("Basic ")


def test_provider_credential_and_invalid_provider_fail_closed() -> None:
    with pytest.raises(ValueError, match="missing or too short"):
        DirectProviderAdapter(DirectProviderConfig("x", "sendgrid", "TOKEN"), credential="short")
    with pytest.raises(ValueError, match="unsupported direct provider"):
        DirectProviderConfig.from_value(
            {"connectorId": "x", "provider": "unknown", "credentialEnv": "TOKEN"}
        )


@pytest.mark.parametrize(
    ("oauth_issuer", "adapter_provider"),
    [("google", "google_calendar"), ("microsoft", "microsoft_graph")],
)
def test_oauth_provider_issuers_are_normalized_for_direct_dispatch(
    oauth_issuer: str, adapter_provider: str
) -> None:
    config = DirectProviderConfig.from_value(
        {"connectorId": "calendar", "provider": oauth_issuer, "credentialEnv": "TOKEN"}
    )

    assert config.provider == adapter_provider


def test_provider_http_failure_is_typed_and_retryable_only_for_transient_status() -> None:
    transport = Transport({"error": "busy"}, status=503)
    adapter = DirectProviderAdapter(
        DirectProviderConfig("email-sendgrid", "sendgrid", "TOKEN"),
        transport=transport,
        credential="x" * 32,
    )

    with pytest.raises(ProviderAdapterError) as raised:
        adapter.invoke(
            _request("email-sendgrid"),
            json.dumps(
                {
                    "action": "email.send",
                    "to": "buyer@example.test",
                    "from": "agent@example.test",
                    "text": "hello",
                }
            ).encode(),
        )

    assert raised.value.code == "provider_rejected"
    assert raised.value.retryable is True
