"""Production-owned configured ingress adapters.

The adapter deliberately has a small, explicit boundary: provider configuration
selects a tenant, verifies an HMAC over the unmodified webhook body, persists
the encrypted source artifact, and then delegates admission/capture to the
existing OT-01 services.  It does not infer provider identity or tenant scope
from the request body.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from psycopg.types.json import Jsonb

from .artifacts import ArtifactPointer, EncryptedArtifactStore, ObjectStore, StoredObject
from .canonical_repository import Connection
from .capture import FormCapture
from .ingress import InboundEnvelope, IngressAuthenticator, PayloadArtifactVerifier
from .ingress_service import IngressProviderRuntime, IngressService
from .voice_repository import VoiceCallRepository
from .voice_runtime import (
    inbound_voice_event,
    parse_twilio_form,
    render_inbound_ai_disclosure,
    verify_twilio_signature,
)


class ConfiguredIngressError(ValueError):
    """Raised when an owner-supplied ingress adapter is invalid or unavailable."""


@dataclass(frozen=True, slots=True)
class IngressProvider:
    provider_id: str
    tenant_id: str
    provider_account_ref: str
    channel: str
    recipient_endpoint: str
    signature_header: str
    secret: bytes
    event_id_field: str
    sender_field: str
    display_name_field: str | None
    thread_id_field: str | None
    retention_days: int
    signature_mode: str = "hmac_sha256"
    webhook_url: str | None = None
    voice_agent_name: str | None = None
    voice_brokerage_name: str | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> IngressProvider:
        required = {
            "id",
            "tenantId",
            "providerAccountRef",
            "channel",
            "recipientEndpoint",
            "signatureHeader",
            "secretEnv",
            "eventIdField",
            "senderField",
        }
        optional = {
            "displayNameField",
            "threadIdField",
            "retentionDays",
            "signatureMode",
            "webhookUrl",
            "voiceAgentName",
            "voiceBrokerageName",
        }
        if set(value) - (required | optional):
            raise ConfiguredIngressError("ingress provider has unpublished fields")
        if not required.issubset(value):
            raise ConfiguredIngressError("ingress provider is missing required fields")
        provider_id = _required_string(value, "id")
        tenant_id = _required_string(value, "tenantId")
        account = _required_string(value, "providerAccountRef")
        channel = _required_string(value, "channel")
        if channel not in {"form", "email", "sms", "phone"}:
            raise ConfiguredIngressError("ingress provider channel is unsupported")
        secret_env = _required_string(value, "secretEnv")
        secret = os.environ.get(secret_env, "").encode()
        if len(secret) < 32:
            raise ConfiguredIngressError(f"ingress secret env {secret_env} must contain >=32 bytes")
        retention_days = int(value.get("retentionDays", 365))
        if retention_days < 1:
            raise ConfiguredIngressError("ingress retentionDays must be positive")
        signature_mode = str(value.get("signatureMode") or "hmac_sha256")
        if signature_mode not in {"hmac_sha256", "twilio_voice"}:
            raise ConfiguredIngressError("ingress signatureMode is unsupported")
        if signature_mode == "twilio_voice" and channel != "phone":
            raise ConfiguredIngressError("twilio_voice signatureMode requires a phone channel")
        webhook_url = _optional_string(value, "webhookUrl")
        if signature_mode == "twilio_voice" and not webhook_url:
            raise ConfiguredIngressError("twilio_voice ingress requires webhookUrl")
        voice_agent_name = _optional_string(value, "voiceAgentName")
        voice_brokerage_name = _optional_string(value, "voiceBrokerageName")
        if signature_mode == "twilio_voice" and not voice_agent_name:
            raise ConfiguredIngressError("twilio_voice ingress requires voiceAgentName")
        if signature_mode == "twilio_voice" and not voice_brokerage_name:
            raise ConfiguredIngressError("twilio_voice ingress requires voiceBrokerageName")
        return cls(
            provider_id=provider_id,
            tenant_id=tenant_id,
            provider_account_ref=account,
            channel=channel,
            recipient_endpoint=_required_string(value, "recipientEndpoint"),
            signature_header=_required_string(value, "signatureHeader").lower(),
            secret=secret,
            event_id_field=_required_string(value, "eventIdField"),
            sender_field=_required_string(value, "senderField"),
            display_name_field=_optional_string(value, "displayNameField"),
            thread_id_field=_optional_string(value, "threadIdField"),
            retention_days=retention_days,
            signature_mode=signature_mode,
            webhook_url=webhook_url,
            voice_agent_name=voice_agent_name,
            voice_brokerage_name=voice_brokerage_name,
        )


class PostgresIngressObjectStore(ObjectStore):
    """Append-only encrypted object storage in the canonical PostgreSQL deployment."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def put(
        self,
        *,
        tenant_id: str,
        artifact_id: str,
        content: bytes,
        object_lock_until: datetime | None,
        legal_hold: bool,
    ) -> StoredObject:
        reference = (
            "postgres-object:v1:"
            + hashlib.sha256(f"{tenant_id}\x00{artifact_id}".encode()).hexdigest()
        )
        with self._connection.cursor() as cursor:
            _set_tenant(cursor, tenant_id)
            cursor.execute(
                """
                INSERT INTO ingress_artifact_objects
                    (tenant_id, artifact_id, encrypted_object_ref, encrypted_blob,
                     object_lock_until, provider_legal_hold)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, artifact_id) DO NOTHING
                """.strip(),
                (tenant_id, artifact_id, reference, content, object_lock_until, legal_hold),
            )
        return StoredObject(reference, object_lock_until, legal_hold)

    def get(self, *, encrypted_object_ref: str) -> bytes:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT encrypted_blob FROM ingress_artifact_objects "
                "WHERE encrypted_object_ref = %s",
                (encrypted_object_ref,),
            )
            row = cursor.fetchone()
        if row is None:
            raise FileNotFoundError("encrypted ingress artifact does not exist")
        value = row[0]
        if not isinstance(value, bytes | bytearray | memoryview):
            raise TypeError("encrypted ingress artifact is not binary")
        return bytes(value)

    def delete(self, *, encrypted_object_ref: str) -> None:
        raise PermissionError("ingress source artifacts are append-only")


class ConfiguredIngressAuthenticator(IngressAuthenticator):
    def __init__(self, providers: dict[str, IngressProvider]) -> None:
        self._providers = providers

    def authenticate(self, tenant_id: str, envelope: InboundEnvelope) -> bool:
        provider = self._providers.get(envelope.provider_account_ref)
        return bool(
            provider
            and provider.tenant_id == tenant_id
            and envelope.signature_verification == "verified"
        )


class PostgresIngressArtifactVerifier(PayloadArtifactVerifier):
    def __init__(
        self,
        connection: Connection,
        *,
        artifact_store: EncryptedArtifactStore,
    ) -> None:
        self._connection = connection
        self._artifact_store = artifact_store

    def verify_payload(self, tenant_id: str, artifact_id: str, digest: str) -> bool:
        with self._connection.cursor() as cursor:
            _set_tenant(cursor, tenant_id)
            cursor.execute(
                """
                SELECT encrypted_object_ref, encryption_key_ref, artifact_digest,
                       object_lock_until, provider_legal_hold
                FROM evidence_artifact_versions
                WHERE tenant_id = %s AND artifact_id = %s AND version = 1
                  AND artifact_state = 'active'
                """.strip(),
                (tenant_id, artifact_id),
            )
            row = cursor.fetchone()
        if row is None or row[2] != digest or row[0] is None:
            return False
        pointer = ArtifactPointer(
            artifact_id=artifact_id,
            encrypted_object_ref=str(row[0]),
            encryption_key_ref=str(row[1]),
            artifact_digest=str(row[2]),
            object_lock_until=cast(datetime | None, row[3]),
            provider_legal_hold=bool(row[4]),
        )
        try:
            self._artifact_store.get(tenant_id=tenant_id, pointer=pointer)
        except (FileNotFoundError, ValueError, RuntimeError):
            return False
        return True


class ConfiguredIngressRuntimeFactory:
    """Owner-configured HMAC ingress factory and webhook adapter."""

    def __init__(
        self,
        providers: tuple[IngressProvider, ...],
        *,
        artifact_encryption_key: bytes,
        artifact_encryption_key_ref: str,
    ) -> None:
        if len(artifact_encryption_key) != 32:
            raise ConfiguredIngressError("artifact encryption key must be exactly 32 bytes")
        if not artifact_encryption_key_ref:
            raise ConfiguredIngressError("artifact encryption key reference is required")
        by_account = {item.provider_account_ref: item for item in providers}
        by_id = {item.provider_id: item for item in providers}
        if len(by_account) != len(providers) or len(by_id) != len(providers):
            raise ConfiguredIngressError("ingress provider ids and accounts must be unique")
        self._providers = by_account
        self._providers_by_id = by_id
        self._artifact_key = artifact_encryption_key
        self._artifact_key_ref = artifact_encryption_key_ref

    @classmethod
    def from_environment(cls) -> ConfiguredIngressRuntimeFactory:
        encoded = os.environ.get("BUYER_OPS_INGRESS_PROVIDERS_JSON", "").strip()
        if not encoded:
            raise ConfiguredIngressError("BUYER_OPS_INGRESS_PROVIDERS_JSON is required")
        raw = json.loads(encoded)
        if not isinstance(raw, list) or not raw:
            raise ConfiguredIngressError("ingress providers must be a non-empty array")
        providers = tuple(IngressProvider.from_mapping(item) for item in raw)
        encoded_key = os.environ.get("BUYER_OPS_ARTIFACT_ENCRYPTION_KEY_B64", "")
        try:
            key = base64.urlsafe_b64decode(encoded_key + "=" * (-len(encoded_key) % 4))
        except ValueError as exc:
            raise ConfiguredIngressError("artifact encryption key is not valid base64url") from exc
        return cls(
            providers,
            artifact_encryption_key=key,
            artifact_encryption_key_ref=_required_env("BUYER_OPS_ARTIFACT_ENCRYPTION_KEY_REF"),
        )

    def __call__(self, *, connection: Connection, tenant_id: str) -> IngressProviderRuntime:
        providers = {
            provider.provider_account_ref: provider
            for provider in self._providers.values()
            if provider.tenant_id == tenant_id
        }
        if not providers:
            raise ConfiguredIngressError("no ingress provider is configured for tenant")
        artifact_store = EncryptedArtifactStore(
            PostgresIngressObjectStore(connection),
            encryption_key_ref=self._artifact_key_ref,
            encryption_key=self._artifact_key,
        )
        return IngressProviderRuntime(
            authenticator=ConfiguredIngressAuthenticator(providers),
            artifacts=PostgresIngressArtifactVerifier(connection, artifact_store=artifact_store),
            capture=FormCapture(connection, tenant_id=tenant_id),
        )

    def handle_webhook(
        self,
        connection: Connection,
        provider_id: str,
        headers: dict[str, str],
        body: bytes,
    ) -> dict[str, Any]:
        provider = self._providers_by_id.get(provider_id)
        if provider is None:
            raise ConfiguredIngressError("unknown ingress provider")
        if len(body) > 1_048_576:
            raise ConfiguredIngressError("ingress payload exceeds 1 MiB limit")
        signature = _header(headers, provider.signature_header)
        if provider.signature_mode == "twilio_voice":
            try:
                source = parse_twilio_form(body)
            except ValueError as exc:
                raise ConfiguredIngressError(str(exc)) from exc
            if signature is None or not verify_twilio_signature(
                auth_token=provider.secret,
                webhook_url=str(provider.webhook_url),
                params=source,
                signature=signature,
            ):
                raise ConfiguredIngressError("ingress signature verification failed")
            try:
                voice_event = inbound_voice_event(source)
            except ValueError as exc:
                raise ConfiguredIngressError(str(exc)) from exc
            source = dict(source)
            source["voiceEventType"] = "inbound_call"
            source["voiceCallSid"] = voice_event.call_sid
        else:
            expected = "sha256=" + hmac.new(provider.secret, body, hashlib.sha256).hexdigest()
            if signature is None or not hmac.compare_digest(signature, expected):
                raise ConfiguredIngressError("ingress signature verification failed")
            try:
                source = json.loads(body.decode())
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ConfiguredIngressError(
                    "configured ingress requires a JSON webhook body"
                ) from exc
            if not isinstance(source, dict):
                raise ConfiguredIngressError("configured ingress webhook body must be an object")
        event_id = _field(source, provider.event_id_field)
        sender = _field(source, provider.sender_field)
        if not isinstance(event_id, str) or not event_id:
            raise ConfiguredIngressError("configured ingress event id is missing")
        if not isinstance(sender, str) or not sender:
            raise ConfiguredIngressError("configured ingress sender is missing")
        artifact_id = (
            "ingress-artifact:"
            + hashlib.sha256(f"{provider.provider_id}\x00{event_id}".encode()).hexdigest()
        )
        now = datetime.now(UTC)
        lock_until = now + timedelta(days=provider.retention_days)
        store = EncryptedArtifactStore(
            PostgresIngressObjectStore(connection),
            encryption_key_ref=self._artifact_key_ref,
            encryption_key=self._artifact_key,
        )
        pointer = store.put(
            tenant_id=provider.tenant_id,
            artifact_id=artifact_id,
            content=body,
            object_lock_until=lock_until,
            legal_hold=False,
        )
        _record_artifact(connection, provider, pointer, now, lock_until)
        envelope = InboundEnvelope.from_mapping(
            {
                "schemaVersion": "ot01.inbound/1",
                "providerEventId": event_id,
                "providerAccountRef": provider.provider_account_ref,
                "channel": provider.channel,
                "receivedAt": now.isoformat().replace("+00:00", "Z"),
                "senderEndpoint": sender,
                "recipientEndpoint": provider.recipient_endpoint,
                "payloadArtifactId": artifact_id,
                "payloadDigest": pointer.artifact_digest,
                "signatureVerification": "verified",
                "externalThreadId": (
                    _field(source, provider.thread_id_field)
                    if provider.thread_id_field is not None
                    else None
                ),
            }
        )
        display_name = (
            _field(source, provider.display_name_field)
            if provider.display_name_field is not None
            else sender
        )
        result = IngressService(
            connection,
            tenant_id=provider.tenant_id,
            provider_runtime=self(connection=connection, tenant_id=provider.tenant_id),
        ).admit_envelope(
            {
                "envelope": envelope.to_mapping(),
                "identity": {
                    "schemaVersion": "open-019-024/1.1.0",
                    "tenantId": provider.tenant_id,
                    "recordId": f"external-message-identity:{artifact_id}",
                    "recordVersion": 1,
                    "observedAt": envelope.received_at,
                    "effectiveFrom": envelope.received_at,
                    "status": "current",
                    "evidenceRefs": [artifact_id],
                    "recordType": "ExternalMessageIdentity",
                    "connectorId": f"configured-webhook:{provider.provider_id}",
                    "provider": provider.provider_id,
                    "providerAccountRef": provider.provider_account_ref,
                    "externalMessageId": event_id,
                    "externalEventId": event_id,
                    "payloadDigest": pointer.artifact_digest,
                },
                "displayName": display_name if isinstance(display_name, str) else sender,
            }
        )
        if provider.signature_mode == "twilio_voice":
            voice_event = inbound_voice_event(source)
            VoiceCallRepository(connection, tenant_id=provider.tenant_id).record_inbound(
                event_id=event_id,
                provider_account_ref=provider.provider_account_ref,
                event=voice_event,
                observed_at=now,
                payload=source,
                ai_disclosure_delivered=True,
            )
            result = {
                **result,
                "_httpContentType": "application/xml",
                "_httpBody": render_inbound_ai_disclosure(
                    agent_name=str(provider.voice_agent_name),
                    brokerage_name=str(provider.voice_brokerage_name),
                ),
            }
        connection.commit()
        return result


def _record_artifact(
    connection: Connection,
    provider: IngressProvider,
    pointer: ArtifactPointer,
    captured_at: datetime,
    retain_until: datetime,
) -> None:
    with connection.cursor() as cursor:
        _set_tenant(cursor, provider.tenant_id)
        cursor.execute(
            """
            INSERT INTO evidence_artifact_versions (
                tenant_id, artifact_id, version, encrypted_object_ref,
                encryption_key_ref, artifact_digest, provenance, classification,
                retention_class, purpose, captured_at, retain_until,
                object_lock_until, provider_legal_hold, artifact_state
            ) VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
            ON CONFLICT (tenant_id, artifact_id, version) DO NOTHING
            """.strip(),
            (
                provider.tenant_id,
                pointer.artifact_id,
                pointer.encrypted_object_ref,
                pointer.encryption_key_ref,
                pointer.artifact_digest,
                Jsonb({"providerId": provider.provider_id}),
                "confidential",
                "inbound_source",
                "ot01.inbound",
                captured_at,
                retain_until,
                pointer.object_lock_until,
                pointer.provider_legal_hold,
            ),
        )


def _set_tenant(cursor: Any, tenant_id: str) -> None:
    cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))


def _field(value: dict[str, Any], path: str | None) -> Any:
    if not path:
        return None
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _header(headers: dict[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value.strip()
    return None


def _required_string(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ConfiguredIngressError(f"ingress provider field {key} must be non-empty")
    return result.strip()


def _optional_string(value: dict[str, Any], key: str) -> str | None:
    result = value.get(key)
    if result is None:
        return None
    if not isinstance(result, str) or not result.strip():
        raise ConfiguredIngressError(f"ingress provider field {key} must be non-empty when set")
    return result.strip()


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfiguredIngressError(f"{name} is required")
    return value
