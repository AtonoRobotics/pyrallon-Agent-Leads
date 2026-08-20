"""OT-01 ingress admission: envelopes, attribution, and consent presentation evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from psycopg.types.json import Jsonb

from .canonical_repository import Connection
from .ingress import (
    InboundAdmission,
    InboundEnvelope,
    IngressAuthenticator,
    IngressRejected,
    PayloadArtifactVerifier,
    PostgresInboundEventRegistry,
    RegisteredInboundEvent,
)
from .structural import validate_record


class InboundCaptureHandler(Protocol):
    def after_ingress(
        self,
        envelope: InboundEnvelope,
        identity: dict[str, Any],
        registered: RegisteredInboundEvent,
        *,
        display_name: str,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class IngressProviderRuntime:
    authenticator: IngressAuthenticator
    artifacts: PayloadArtifactVerifier
    capture: InboundCaptureHandler


class IngressService:
    def __init__(
        self,
        connection: Connection,
        *,
        tenant_id: str,
        provider_runtime: IngressProviderRuntime | None = None,
    ) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._connection = connection
        self._tenant_id = tenant_id
        self._provider_runtime = provider_runtime

    def admit(self, message: dict[str, Any]) -> dict[str, Any]:
        validate_record(message, "ot01_ingress")
        tenant_field = "tenantId"
        if message.get(tenant_field) != self._tenant_id:
            raise ValueError("ingress tenant does not match authenticated tenant")
        message_type = message["messageType"]
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
                if message_type == "attribution_input":
                    cursor.execute(
                        """
                        INSERT INTO ingress_attribution (
                            tenant_id, attribution_id, payload, payload_digest
                        ) VALUES (%s, %s, %s, %s)
                        ON CONFLICT (tenant_id, attribution_id) DO NOTHING
                        """.strip(),
                        (
                            self._tenant_id,
                            message["attributionId"],
                            Jsonb(message),
                            message["payloadDigest"],
                        ),
                    )
                    cursor.execute(
                        """
                        SELECT payload FROM ingress_attribution
                        WHERE tenant_id = %s AND attribution_id = %s
                        """.strip(),
                        (self._tenant_id, message["attributionId"]),
                    )
                    row = cursor.fetchone()
                    if row is None or not isinstance(row[0], dict):
                        raise RuntimeError("attribution disappeared after admission")
                    stored = row[0]
                    if stored != message:
                        raise ValueError("duplicate attribution with different evidence")
                elif message_type == "consent_presentation_evidence":
                    cursor.execute(
                        """
                        INSERT INTO ingress_consent_presentation (
                            tenant_id, evidence_id, payload, payload_digest
                        ) VALUES (%s, %s, %s, %s)
                        ON CONFLICT (tenant_id, evidence_id) DO NOTHING
                        """.strip(),
                        (
                            self._tenant_id,
                            message["evidenceId"],
                            Jsonb(message),
                            message["payloadDigest"],
                        ),
                    )
                    cursor.execute(
                        """
                        SELECT payload FROM ingress_consent_presentation
                        WHERE tenant_id = %s AND evidence_id = %s
                        """.strip(),
                        (self._tenant_id, message["evidenceId"]),
                    )
                    row = cursor.fetchone()
                    if row is None or not isinstance(row[0], dict):
                        raise RuntimeError("consent presentation disappeared after admission")
                    stored = row[0]
                    if stored != message:
                        raise ValueError("duplicate consent presentation with different evidence")
                else:
                    raise ValueError("unsupported ingress message")
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return stored if isinstance(stored, dict) else message

    def admit_envelope(self, payload: dict[str, Any]) -> dict[str, Any]:
        runtime = self._provider_runtime
        if runtime is None:
            raise IngressRejected("configuration_incomplete")
        envelope = InboundEnvelope.from_mapping(payload["envelope"])
        identity = payload["identity"]
        tenant_id = self._tenant_id

        admission = InboundAdmission(
            runtime.authenticator,
            runtime.artifacts,
            PostgresInboundEventRegistry(self._connection),
        )
        registered = admission.admit(tenant_id, envelope, identity)
        capture = runtime.capture.after_ingress(
            envelope,
            identity,
            registered,
            display_name=str(payload.get("displayName") or envelope.sender_endpoint),
        )
        return {
            "event_id": registered.event_id,
            "duplicate": registered.duplicate,
            "duplicate_of": registered.duplicate_of,
            "person_id": capture.get("person_id"),
            "journey_id": capture.get("journey_id"),
            "mapping_id": capture.get("mapping_id"),
        }
