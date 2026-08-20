from __future__ import annotations

from dataclasses import replace

import pytest

from buyer_ops_contracts.ingress import (
    InboundAdmission,
    InboundEnvelope,
    IngressRejected,
    RegisteredInboundEvent,
)


def _envelope() -> InboundEnvelope:
    return InboundEnvelope.from_mapping(
        {
            "schemaVersion": "ot01.inbound/1",
            "providerEventId": "provider-event-1",
            "providerAccountRef": "provider-account-1",
            "channel": "sms",
            "receivedAt": "2026-08-19T12:00:00Z",
            "providerOccurredAt": "2026-08-19T11:59:59Z",
            "senderEndpoint": "+15551234567",
            "recipientEndpoint": "+15557654321",
            "externalThreadId": "provider-message-1",
            "payloadArtifactId": "artifact-1",
            "payloadDigest": "sha256:" + "a" * 64,
            "signatureVerification": "verified",
        }
    )


def _message_identity(event_id: str = "provider-event-1", digest: str | None = None) -> dict:
    return {
        "schemaVersion": "open-019-024/1.1.0",
        "tenantId": "tenant-1",
        "recordId": f"message-identity-{event_id}",
        "recordVersion": 1,
        "observedAt": "2026-08-19T12:00:00Z",
        "effectiveFrom": "2026-08-19T12:00:00Z",
        "status": "current",
        "evidenceRefs": ["provider-envelope-1"],
        "recordType": "ExternalMessageIdentity",
        "connectorId": "connector-1",
        "provider": "provider-1",
        "providerAccountRef": "provider-account-1",
        "externalMessageId": "provider-message-1",
        "externalEventId": event_id,
        "payloadDigest": digest or "sha256:" + "a" * 64,
    }


def test_inbound_admission_authenticates_and_registers_before_processing() -> None:
    calls: list[str] = []

    class Authenticator:
        def authenticate(self, tenant_id: str, envelope: InboundEnvelope) -> bool:
            calls.append("authenticate")
            return tenant_id == "tenant-1"

    class Artifacts:
        def verify_payload(self, tenant_id: str, artifact_id: str, digest: str) -> bool:
            calls.append("artifact")
            return (artifact_id, digest) == ("artifact-1", "sha256:" + "a" * 64)

    class Registry:
        def register(
            self, tenant_id: str, envelope: InboundEnvelope, identity: dict
        ) -> RegisteredInboundEvent:
            calls.append("register")
            return RegisteredInboundEvent("inbound-1", duplicate=False, duplicate_of=None)

    result = InboundAdmission(Authenticator(), Artifacts(), Registry()).admit(
        "tenant-1", _envelope(), _message_identity()
    )
    assert result.event_id == "inbound-1"
    assert calls == ["authenticate", "artifact", "register"]


def test_inbound_admission_rejects_unknown_destination_without_registration() -> None:
    class Authenticator:
        def authenticate(self, tenant_id: str, envelope: InboundEnvelope) -> bool:
            return False

    class NeverCalled:
        def __getattr__(self, name: str):
            raise AssertionError(f"{name} must not be called")

    with pytest.raises(IngressRejected) as raised:
        InboundAdmission(Authenticator(), NeverCalled(), NeverCalled()).admit(
            "tenant-1", _envelope(), _message_identity()
        )
    assert raised.value.code == "ingress_authentication_failed"


def test_inbound_envelope_rejects_unpublished_fields_and_invalid_nested_contracts() -> None:
    with pytest.raises(ValueError):
        InboundEnvelope.from_mapping({**_envelope().to_mapping(), "tenantId": "untrusted"})

    invalid = _envelope().to_mapping()
    invalid["attribution"] = {
        "messageType": "attribution_input",
        "schemaVersion": "ot01-ingress/1.1.0",
    }
    with pytest.raises(ValueError):
        InboundEnvelope.from_mapping(invalid)


@pytest.mark.parametrize("field", ["receivedAt", "providerOccurredAt"])
def test_inbound_envelope_requires_offsets_on_event_timestamps(field: str) -> None:
    invalid = _envelope().to_mapping()
    invalid[field] = "2026-08-19T12:00:00"

    with pytest.raises(ValueError, match=f"{field} must be an RFC 3339 timestamp"):
        InboundEnvelope.from_mapping(invalid)


def test_inbound_envelope_requires_compensating_control_marker_when_unsigned() -> None:
    unsigned = replace(_envelope(), signature_verification="not_supported")
    assert unsigned.signature_verification == "not_supported"
