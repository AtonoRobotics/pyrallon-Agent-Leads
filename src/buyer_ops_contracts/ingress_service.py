"""OT-01 ingress admission: envelopes, attribution, and consent presentation evidence."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from psycopg.types.json import Jsonb

from .canonical_repository import Connection
from .capture import FormCapture
from .ingress import (
    InboundAdmission,
    InboundEnvelope,
    PostgresInboundEventRegistry,
)
from .structural import validate_record


class IngressService:
    def __init__(self, connection: Connection, *, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._connection = connection
        self._tenant_id = tenant_id

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
                            tenant_id, attribution_id, payload, payload_digest, received_at
                        ) VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (tenant_id, attribution_id) DO NOTHING
                        """.strip(),
                        (
                            self._tenant_id,
                            message["attributionId"],
                            Jsonb(message),
                            message["payloadDigest"],
                            message["receivedAt"],
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
                    stored = row[0] if row else message
                    if (
                        isinstance(stored, dict)
                        and stored.get("payloadDigest") != message["payloadDigest"]
                    ):
                        raise ValueError("duplicate attribution with a different payload digest")
                elif message_type == "consent_presentation_evidence":
                    cursor.execute(
                        """
                        INSERT INTO ingress_consent_presentation (
                            tenant_id, evidence_id, payload, payload_digest, presented_at
                        ) VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (tenant_id, evidence_id) DO NOTHING
                        """.strip(),
                        (
                            self._tenant_id,
                            message["evidenceId"],
                            Jsonb(message),
                            message["payloadDigest"],
                            message["presentedAt"],
                        ),
                    )
                    stored = message
                else:
                    raise ValueError("unsupported ingress message")
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return stored if isinstance(stored, dict) else message

    def admit_envelope(self, payload: dict[str, Any]) -> dict[str, Any]:
        envelope = InboundEnvelope.from_mapping(payload["envelope"])
        identity = payload["identity"]
        raw = str(payload.get("payload", "")).encode()
        tenant_id = self._tenant_id

        class _Auth:
            def authenticate(self, admitted_tenant: str, admitted: InboundEnvelope) -> bool:
                return (
                    admitted_tenant == tenant_id
                    and admitted.channel == "form"
                    and admitted.signature_verification == "not_supported"
                )

        class _Artifacts:
            def verify_payload(self, admitted_tenant: str, artifact_id: str, digest: str) -> bool:
                del admitted_tenant, artifact_id
                return digest == f"sha256:{sha256(raw).hexdigest()}"

        admission = InboundAdmission(
            _Auth(),
            _Artifacts(),
            PostgresInboundEventRegistry(self._connection),
        )
        registered = admission.admit(tenant_id, envelope, identity)
        capture = FormCapture(self._connection, tenant_id=tenant_id).after_ingress(
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
