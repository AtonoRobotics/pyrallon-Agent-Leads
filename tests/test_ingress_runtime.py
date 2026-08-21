import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

from buyer_ops_contracts.ingress_runtime import (
    ConfiguredIngressError,
    ConfiguredIngressRuntimeFactory,
    IngressProvider,
)


class _Cursor:
    def __init__(self, connection: "_Connection") -> None:
        self.connection = connection
        self.row: tuple[object, ...] | None = None

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        if "INSERT INTO ingress_artifact_objects" in query:
            self.connection.blob = params[3]
        elif "FROM evidence_artifact_versions" in query:
            self.row = (
                "postgres-object:v1:ref",
                "key-v1",
                params[1] if len(params) > 1 else self.connection.digest,
                datetime.now(UTC),
                False,
            )
        elif "SELECT encrypted_blob" in query:
            self.row = (self.connection.blob,)
        else:
            self.row = None

    def fetchone(self) -> tuple[object, ...] | None:
        return self.row


class _Connection:
    def __init__(self) -> None:
        self.blob: object = b""
        self.digest = "sha256:" + "0" * 64

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        return None


def _factory() -> ConfiguredIngressRuntimeFactory:
    provider = IngressProvider(
        provider_id="primary-form",
        tenant_id="tenant-1",
        provider_account_ref="account-1",
        channel="form",
        recipient_endpoint="intake",
        signature_header="x-provider-signature",
        secret=b"s" * 32,
        event_id_field="event.id",
        sender_field="lead.email",
        display_name_field="lead.name",
        thread_id_field="thread.id",
        retention_days=30,
    )
    return ConfiguredIngressRuntimeFactory(
        (provider,), artifact_encryption_key=b"k" * 32, artifact_encryption_key_ref="key-v1"
    )


def test_provider_config_rejects_unpublished_fields() -> None:
    value = {
        "id": "primary-form",
        "tenantId": "tenant-1",
        "providerAccountRef": "account-1",
        "channel": "form",
        "recipientEndpoint": "intake",
        "signatureHeader": "x-provider-signature",
        "secretEnv": "SECRET",
        "eventIdField": "event.id",
        "senderField": "lead.email",
        "unpublished": True,
    }
    try:
        IngressProvider.from_mapping(value)
    except ConfiguredIngressError:
        pass
    else:
        raise AssertionError("unpublished provider fields must be rejected")


def test_provider_config_accepts_phone_webhooks(monkeypatch) -> None:
    value = {
        "id": "primary-phone",
        "tenantId": "tenant-1",
        "providerAccountRef": "account-phone",
        "channel": "phone",
        "recipientEndpoint": "+15557654321",
        "signatureHeader": "x-provider-signature",
        "secretEnv": "SECRET",
        "eventIdField": "event.id",
        "senderField": "call.from",
    }
    monkeypatch.setenv("SECRET", "s" * 32)
    assert IngressProvider.from_mapping(value).channel == "phone"


def test_provider_config_accepts_twilio_voice_signature_mode(monkeypatch) -> None:
    value = {
        "id": "primary-phone",
        "tenantId": "tenant-1",
        "providerAccountRef": "account-phone",
        "channel": "phone",
        "recipientEndpoint": "+15557654321",
        "signatureHeader": "x-twilio-signature",
        "signatureMode": "twilio_voice",
        "webhookUrl": "https://voice.example.test/v1/ingress/webhook/primary-phone",
        "voiceAgentName": "Alex Agent",
        "voiceBrokerageName": "Example Realty",
        "secretEnv": "SECRET",
        "eventIdField": "CallSid",
        "senderField": "From",
    }
    monkeypatch.setenv("SECRET", "s" * 32)
    provider = IngressProvider.from_mapping(value)
    assert provider.signature_mode == "twilio_voice"
    assert provider.webhook_url == value["webhookUrl"]


def test_webhook_rejects_invalid_signature_before_persistence() -> None:
    body = json.dumps({"event": {"id": "evt-1"}, "lead": {"email": "buyer@example.com"}}).encode()
    try:
        _factory().handle_webhook(
            _Connection(),
            "primary-form",
            {"x-provider-signature": "sha256=" + "0" * 64},
            body,
        )
    except ConfiguredIngressError as exc:
        assert "signature" in str(exc)
    else:
        raise AssertionError("invalid webhook signature must be rejected")


def test_webhook_verifies_signature_and_persists_encrypted_source(monkeypatch) -> None:
    body = json.dumps(
        {
            "event": {"id": "evt-1"},
            "lead": {"email": "buyer@example.com", "name": "Buyer"},
            "thread": {"id": "thread-1"},
        },
        separators=(",", ":"),
    ).encode()
    secret = b"s" * 32
    signature = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    connection = _Connection()

    class _Service:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def admit_envelope(self, payload):
            assert payload["envelope"]["signatureVerification"] == "verified"
            assert payload["identity"]["externalMessageId"] == "evt-1"
            return {"journey_id": "journey-1", "duplicate": False}

    monkeypatch.setattr("buyer_ops_contracts.ingress_runtime.IngressService", _Service)
    result = _factory().handle_webhook(
        connection,
        "primary-form",
        {"X-Provider-Signature": signature},
        body,
    )
    assert result == {"journey_id": "journey-1", "duplicate": False}
    assert isinstance(connection.blob, bytes)
    assert connection.blob != body


def test_twilio_voice_webhook_verifies_form_signature_and_admits_phone(monkeypatch) -> None:
    provider = IngressProvider(
        provider_id="primary-phone",
        tenant_id="tenant-1",
        provider_account_ref="account-phone",
        channel="phone",
        recipient_endpoint="+15557654321",
        signature_header="x-twilio-signature",
        secret=b"s" * 32,
        event_id_field="CallSid",
        sender_field="From",
        display_name_field="CallerName",
        thread_id_field="CallSid",
        retention_days=30,
        signature_mode="twilio_voice",
        webhook_url="https://voice.example.test/v1/ingress/webhook/primary-phone",
        voice_agent_name="Alex Agent",
        voice_brokerage_name="Example Realty",
    )
    factory = ConfiguredIngressRuntimeFactory(
        (provider,), artifact_encryption_key=b"k" * 32, artifact_encryption_key_ref="key-v1"
    )
    body = b"CallSid=CA-1&From=%2B15125550100&To=%2B15557654321&CallStatus=ringing&CallerName=Buyer"
    from buyer_ops_contracts.voice_runtime import parse_twilio_form

    params = parse_twilio_form(body)
    signing_material = provider.webhook_url + "".join(key + params[key] for key in sorted(params))
    signature = base64.b64encode(
        hmac.new(provider.secret, signing_material.encode(), hashlib.sha1).digest()
    ).decode()

    class _Service:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def admit_envelope(self, payload):
            assert payload["envelope"]["channel"] == "phone"
            assert payload["envelope"]["providerEventId"] == "CA-1"
            return {"journey_id": "journey-voice-1", "duplicate": False}

    monkeypatch.setattr("buyer_ops_contracts.ingress_runtime.IngressService", _Service)
    result = factory.handle_webhook(
        _Connection(),
        "primary-phone",
        {"x-twilio-signature": signature},
        body,
    )
    assert result["journey_id"] == "journey-voice-1"
    assert result["_httpContentType"] == "application/xml"
    assert "artificial intelligence assistant" in result["_httpBody"]
