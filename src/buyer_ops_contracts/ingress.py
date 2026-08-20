"""Provider-neutral OT-01 inbound admission before domain processing."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from psycopg.types.json import Jsonb

from .canonical_repository import Connection
from .closure import validate_closure_semantics
from .structural import validate_record

_DIGEST = re.compile(r"^[a-zA-Z0-9_-]+:[a-fA-F0-9]{32,}$")
_REQUIRED = {
    "schemaVersion",
    "providerEventId",
    "providerAccountRef",
    "channel",
    "receivedAt",
    "senderEndpoint",
    "recipientEndpoint",
    "payloadArtifactId",
    "payloadDigest",
    "signatureVerification",
}
_OPTIONAL = {
    "providerOccurredAt",
    "externalThreadId",
    "replyToMessageId",
    "attribution",
    "consentPresentation",
}


class IngressRejected(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class InboundEnvelope:
    schema_version: Literal["ot01.inbound/1"]
    provider_event_id: str
    provider_account_ref: str
    channel: Literal["form", "email", "sms"]
    received_at: str
    sender_endpoint: str
    recipient_endpoint: str
    payload_artifact_id: str
    payload_digest: str
    signature_verification: Literal["verified", "not_supported"]
    provider_occurred_at: str | None = None
    external_thread_id: str | None = None
    reply_to_message_id: str | None = None
    attribution: dict[str, Any] | None = None
    consent_presentation: dict[str, Any] | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> InboundEnvelope:
        if set(value) - (_REQUIRED | _OPTIONAL) or not _REQUIRED.issubset(value):
            raise ValueError("inbound envelope has missing or unpublished fields")
        if value["schemaVersion"] != "ot01.inbound/1":
            raise ValueError("unsupported inbound envelope version")
        if value["channel"] not in {"form", "email", "sms"}:
            raise ValueError("unsupported inbound channel")
        if value["signatureVerification"] not in {"verified", "not_supported"}:
            raise ValueError("invalid signature verification disposition")
        for field in _REQUIRED - {"channel", "signatureVerification"}:
            if not isinstance(value[field], str) or not value[field]:
                raise ValueError(f"{field} must be a non-empty string")
        for field in {"receivedAt", "providerOccurredAt"} & set(value):
            try:
                parsed = datetime.fromisoformat(value[field].replace("Z", "+00:00"))
                if parsed.utcoffset() is None:
                    raise ValueError("timestamp offset is required")
            except (AttributeError, ValueError) as exc:
                raise ValueError(f"{field} must be an RFC 3339 timestamp") from exc
        if not _DIGEST.fullmatch(value["payloadDigest"]):
            raise ValueError("payloadDigest is invalid")
        if value.get("attribution") is not None:
            validate_record(value["attribution"], "ot01_ingress")
            if value["attribution"].get("messageType") != "attribution_input":
                raise ValueError("attribution must be AttributionInput")
        if value.get("consentPresentation") is not None:
            validate_record(value["consentPresentation"], "ot01_ingress")
            if value["consentPresentation"].get("messageType") != "consent_presentation_evidence":
                raise ValueError("consentPresentation must be ConsentPresentationEvidence")
        return cls(
            schema_version=value["schemaVersion"],
            provider_event_id=value["providerEventId"],
            provider_account_ref=value["providerAccountRef"],
            channel=value["channel"],
            received_at=value["receivedAt"],
            sender_endpoint=value["senderEndpoint"],
            recipient_endpoint=value["recipientEndpoint"],
            payload_artifact_id=value["payloadArtifactId"],
            payload_digest=value["payloadDigest"],
            signature_verification=value["signatureVerification"],
            provider_occurred_at=value.get("providerOccurredAt"),
            external_thread_id=value.get("externalThreadId"),
            reply_to_message_id=value.get("replyToMessageId"),
            attribution=value.get("attribution"),
            consent_presentation=value.get("consentPresentation"),
        )

    def to_mapping(self) -> dict[str, Any]:
        pairs = {
            "schemaVersion": self.schema_version,
            "providerEventId": self.provider_event_id,
            "providerAccountRef": self.provider_account_ref,
            "channel": self.channel,
            "receivedAt": self.received_at,
            "senderEndpoint": self.sender_endpoint,
            "recipientEndpoint": self.recipient_endpoint,
            "payloadArtifactId": self.payload_artifact_id,
            "payloadDigest": self.payload_digest,
            "signatureVerification": self.signature_verification,
            "providerOccurredAt": self.provider_occurred_at,
            "externalThreadId": self.external_thread_id,
            "replyToMessageId": self.reply_to_message_id,
            "attribution": self.attribution,
            "consentPresentation": self.consent_presentation,
        }
        return {key: value for key, value in pairs.items() if value is not None}


@dataclass(frozen=True, slots=True)
class RegisteredInboundEvent:
    event_id: str
    duplicate: bool
    duplicate_of: str | None


class IngressAuthenticator(Protocol):
    def authenticate(self, tenant_id: str, envelope: InboundEnvelope) -> bool: ...


class PayloadArtifactVerifier(Protocol):
    def verify_payload(self, tenant_id: str, artifact_id: str, digest: str) -> bool: ...


class InboundEventRegistry(Protocol):
    def register(
        self, tenant_id: str, envelope: InboundEnvelope, identity: dict[str, Any]
    ) -> RegisteredInboundEvent: ...


class InboundAdmission:
    """Authenticate, evidence-check, then durably deduplicate an inbound event."""

    def __init__(
        self,
        authenticator: IngressAuthenticator,
        artifacts: PayloadArtifactVerifier,
        registry: InboundEventRegistry,
    ) -> None:
        self._authenticator = authenticator
        self._artifacts = artifacts
        self._registry = registry

    def admit(
        self,
        tenant_id: str,
        envelope: InboundEnvelope,
        identity: dict[str, Any],
    ) -> RegisteredInboundEvent:
        _validate_external_message_identity(tenant_id, envelope, identity)
        if not tenant_id or not self._authenticator.authenticate(tenant_id, envelope):
            raise IngressRejected("ingress_authentication_failed")
        if not self._artifacts.verify_payload(
            tenant_id, envelope.payload_artifact_id, envelope.payload_digest
        ):
            raise IngressRejected("payload_artifact_mismatch")
        return self._registry.register(tenant_id, envelope, identity)


class PostgresInboundEventRegistry:
    """Linearize exact provider-event replay before any domain processing."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def register(
        self, tenant_id: str, envelope: InboundEnvelope, identity: dict[str, Any]
    ) -> RegisteredInboundEvent:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        _validate_external_message_identity(tenant_id, envelope, identity)
        identity_bytes = json.dumps(
            [
                tenant_id,
                identity["connectorId"],
                identity["providerAccountRef"],
                identity["externalMessageId"],
            ],
            separators=(",", ":"),
        ).encode()
        event_id = f"inbound:{hashlib.sha256(identity_bytes).hexdigest()}"
        connector_id = identity["connectorId"]
        external_message_id = identity["externalMessageId"]
        conflict = False
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                cursor.execute(
                    """
                    INSERT INTO inbound_events (
                        tenant_id, inbound_event_id, provider_account_ref, provider_event_id,
                        channel, received_at, payload_artifact_id, payload_digest, envelope,
                        connector_id, external_message_id, external_event_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, connector_id, provider_account_ref, external_message_id)
                    WHERE connector_id IS NOT NULL AND external_message_id IS NOT NULL
                    DO NOTHING
                    RETURNING inbound_event_id
                    """.strip(),
                    (
                        tenant_id,
                        event_id,
                        envelope.provider_account_ref,
                        envelope.provider_event_id,
                        envelope.channel,
                        envelope.received_at,
                        envelope.payload_artifact_id,
                        envelope.payload_digest,
                        Jsonb(envelope.to_mapping()),
                        connector_id,
                        external_message_id,
                        envelope.provider_event_id,
                    ),
                )
                created = cursor.fetchone() is not None
                if not created:
                    cursor.execute(
                        """
                        SELECT inbound_event_id, payload_digest
                        FROM inbound_events
                        WHERE tenant_id = %s AND connector_id = %s
                            AND provider_account_ref = %s AND external_message_id = %s
                        """.strip(),
                        (
                            tenant_id,
                            connector_id,
                            envelope.provider_account_ref,
                            external_message_id,
                        ),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise RuntimeError("inbound replay disappeared during registration")
                    if row[1] != envelope.payload_digest:
                        cursor.execute(
                            """
                            INSERT INTO inbound_message_conflicts (
                                tenant_id, conflict_id, connector_id, provider_account_ref,
                                external_message_id, original_event_id, conflicting_event_id,
                                original_payload_digest, conflicting_payload_digest
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (tenant_id, conflict_id) DO NOTHING
                            """.strip(),
                            (
                                tenant_id,
                                f"conflict:{hashlib.sha256((event_id + envelope.provider_event_id).encode()).hexdigest()}",
                                connector_id,
                                envelope.provider_account_ref,
                                external_message_id,
                                row[0],
                                envelope.provider_event_id,
                                row[1],
                                envelope.payload_digest,
                            ),
                        )
                        conflict = True
                    event_id = str(row[0])
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        if conflict:
            raise IngressRejected("reconciliation_required")
        return RegisteredInboundEvent(
            event_id=event_id,
            duplicate=not created,
            duplicate_of=None if created else event_id,
        )


def _validate_external_message_identity(
    tenant_id: str, envelope: InboundEnvelope, identity: dict[str, Any]
) -> None:
    validate_record(identity, "closure")
    validate_closure_semantics(identity)
    if (
        identity.get("recordType") != "ExternalMessageIdentity"
        or identity.get("tenantId") != tenant_id
        or identity.get("providerAccountRef") != envelope.provider_account_ref
        or identity.get("externalEventId") != envelope.provider_event_id
        or identity.get("payloadDigest") != envelope.payload_digest
    ):
        raise IngressRejected("external_message_identity_mismatch")
