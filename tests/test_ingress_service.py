from __future__ import annotations

import pytest

from buyer_ops_contracts.ingress import InboundEnvelope, IngressRejected, RegisteredInboundEvent
from buyer_ops_contracts.ingress_service import IngressProviderRuntime, IngressService


class _Cursor:
    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...]) -> _Cursor:
        del statement, parameters
        return self

    def fetchone(self) -> tuple[str]:
        return ("inbound-1",)


class _Connection:
    def cursor(self) -> _Cursor:
        return _Cursor()

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        raise AssertionError("valid ingress must not roll back")


def test_envelope_admission_requires_injected_provider_configuration() -> None:
    service = IngressService(object(), tenant_id="tenant-1")  # type: ignore[arg-type]

    with pytest.raises(IngressRejected) as raised:
        service.admit_envelope({})

    assert raised.value.code == "configuration_incomplete"


def test_envelope_admission_uses_injected_provider_boundaries() -> None:
    calls: list[str] = []
    seen_event_ids: list[str] = []

    class _Authenticator:
        def authenticate(self, tenant_id: str, envelope: InboundEnvelope) -> bool:
            calls.append("authenticate")
            return tenant_id == "tenant-1" and envelope.signature_verification == "verified"

    class _Artifacts:
        def verify_payload(self, tenant_id: str, artifact_id: str, digest: str) -> bool:
            calls.append("artifact")
            return (tenant_id, artifact_id, digest) == (
                "tenant-1",
                "artifact-1",
                "sha256:" + "a" * 64,
            )

    class _Capture:
        def after_ingress(
            self,
            envelope: InboundEnvelope,
            identity: dict[str, object],
            registered: RegisteredInboundEvent,
            *,
            display_name: str,
        ) -> dict[str, str]:
            calls.append("capture")
            assert envelope.channel == "sms"
            assert identity["recordId"] == "message-identity-1"
            assert registered.event_id.startswith("inbound:")
            seen_event_ids.append(registered.event_id)
            assert display_name == "+15551234567"
            return {"person_id": "person-1", "journey_id": "journey-1", "mapping_id": "map-1"}

    service = IngressService(
        _Connection(),  # type: ignore[arg-type]
        tenant_id="tenant-1",
        provider_runtime=IngressProviderRuntime(
            authenticator=_Authenticator(),
            artifacts=_Artifacts(),
            capture=_Capture(),
        ),
    )
    result = service.admit_envelope(
        {
            "envelope": {
                "schemaVersion": "ot01.inbound/1",
                "providerEventId": "provider-event-1",
                "providerAccountRef": "provider-account-1",
                "channel": "sms",
                "receivedAt": "2026-08-19T12:00:00Z",
                "senderEndpoint": "+15551234567",
                "recipientEndpoint": "+15557654321",
                "payloadArtifactId": "artifact-1",
                "payloadDigest": "sha256:" + "a" * 64,
                "signatureVerification": "verified",
            },
            "identity": {
                "schemaVersion": "open-019-024/1.1.0",
                "tenantId": "tenant-1",
                "recordId": "message-identity-1",
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
                "externalEventId": "provider-event-1",
                "payloadDigest": "sha256:" + "a" * 64,
            },
        }
    )

    assert calls == ["authenticate", "artifact", "capture"]
    assert result == {
        "event_id": seen_event_ids[0],
        "duplicate": False,
        "duplicate_of": None,
        "person_id": "person-1",
        "journey_id": "journey-1",
        "mapping_id": "map-1",
    }
