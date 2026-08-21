"""Durable PostgreSQL persistence for the inbound voice lifecycle."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from .canonical_repository import Connection
from .voice_runtime import InboundVoiceEvent, recording_transition


class VoiceCallRepository:
    def __init__(self, connection: Connection, *, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._connection = connection
        self._tenant_id = tenant_id

    def record_inbound(
        self,
        *,
        event_id: str,
        provider_account_ref: str,
        event: InboundVoiceEvent,
        observed_at: datetime,
        payload: dict[str, Any],
        ai_disclosure_delivered: bool = False,
    ) -> bool:
        """Append one provider event and project it into current state idempotently."""
        if not event_id or not provider_account_ref:
            raise ValueError("voice event identity is required")
        with self._connection.cursor() as cursor:
            self._set_tenant(cursor)
            cursor.execute(
                """
                INSERT INTO voice_call_events
                    (tenant_id,event_id,call_sid,provider_account_ref,event_type,payload,observed_at)
                VALUES (%s,%s,%s,%s,'inbound_call',%s,%s)
                ON CONFLICT (tenant_id,event_id) DO NOTHING
                """.strip(),
                (
                    self._tenant_id,
                    event_id,
                    event.call_sid,
                    provider_account_ref,
                    Jsonb(payload),
                    observed_at,
                ),
            )
            cursor.execute(
                """
                INSERT INTO voice_call_current
                    (tenant_id,call_sid,provider_account_ref,from_number,to_number,
                     lifecycle_state,ai_disclosure_state,last_event_id,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_id,call_sid) DO UPDATE SET
                    lifecycle_state = EXCLUDED.lifecycle_state,
                    ai_disclosure_state = CASE
                        WHEN EXCLUDED.ai_disclosure_state = 'delivered' THEN 'delivered'
                        ELSE voice_call_current.ai_disclosure_state
                    END,
                    last_event_id = EXCLUDED.last_event_id,
                    updated_at = EXCLUDED.updated_at
                WHERE voice_call_current.last_event_id <> EXCLUDED.last_event_id
                """.strip(),
                (
                    self._tenant_id,
                    event.call_sid,
                    provider_account_ref,
                    event.from_number,
                    event.to_number,
                    _lifecycle_state(event.call_status),
                    "delivered" if ai_disclosure_delivered else "pending",
                    event_id,
                    observed_at,
                ),
            )
        return True

    def set_recording_consent(
        self,
        *,
        call_sid: str,
        evidence_id: str,
        affirmative: bool,
        observed_at: datetime,
        event_id: str,
    ) -> str:
        """Record explicit consent/refusal and reject invalid state transitions."""
        if not all((call_sid, evidence_id, event_id)):
            raise ValueError("recording transition identity is required")
        with self._connection.cursor() as cursor:
            self._set_tenant(cursor)
            cursor.execute(
                """
                SELECT recording_state FROM voice_call_current
                WHERE tenant_id = %s AND call_sid = %s FOR UPDATE
                """.strip(),
                (self._tenant_id, call_sid),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("voice call does not exist")
            state = recording_transition(current=str(row[0]), affirmative=affirmative)
            cursor.execute(
                """
                INSERT INTO voice_call_events
                    (tenant_id,event_id,call_sid,provider_account_ref,event_type,payload,observed_at)
                SELECT %s,%s,call_sid,provider_account_ref,%s,%s,%s
                FROM voice_call_current
                WHERE tenant_id = %s AND call_sid = %s
                ON CONFLICT (tenant_id,event_id) DO NOTHING
                """.strip(),
                (
                    self._tenant_id,
                    event_id,
                    "recording_consent" if affirmative else "recording_refusal",
                    Jsonb({"evidenceId": evidence_id, "affirmative": affirmative}),
                    observed_at,
                    self._tenant_id,
                    call_sid,
                ),
            )
            cursor.execute(
                """
                UPDATE voice_call_current
                SET recording_state = %s,
                    recording_consent_evidence_id = %s,
                    recording_consent_at = CASE WHEN %s THEN %s ELSE recording_consent_at END,
                    recording_refusal_at = CASE WHEN %s THEN recording_refusal_at ELSE %s END,
                    last_event_id = %s,
                    updated_at = %s
                WHERE tenant_id = %s AND call_sid = %s
                """.strip(),
                (
                    state,
                    evidence_id,
                    affirmative,
                    observed_at,
                    affirmative,
                    observed_at,
                    event_id,
                    observed_at,
                    self._tenant_id,
                    call_sid,
                ),
            )
        return state

    def revoke_recording(
        self,
        *,
        call_sid: str,
        evidence_id: str,
        observed_at: datetime,
        event_id: str,
    ) -> None:
        """Permanently revoke recording for a call that previously consented."""
        if not all((call_sid, evidence_id, event_id)):
            raise ValueError("recording revocation identity is required")
        with self._connection.cursor() as cursor:
            self._set_tenant(cursor)
            cursor.execute(
                """
                SELECT provider_account_ref, recording_state
                FROM voice_call_current
                WHERE tenant_id = %s AND call_sid = %s FOR UPDATE
                """.strip(),
                (self._tenant_id, call_sid),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError("voice call does not exist")
            if str(row[1]) != "consented":
                raise ValueError("recording can only be revoked after consent")
            cursor.execute(
                """
                INSERT INTO voice_call_events
                    (tenant_id,event_id,call_sid,provider_account_ref,event_type,payload,observed_at)
                VALUES (%s,%s,%s,%s,'recording_revocation',%s,%s)
                ON CONFLICT (tenant_id,event_id) DO NOTHING
                """.strip(),
                (
                    self._tenant_id,
                    event_id,
                    call_sid,
                    str(row[0]),
                    Jsonb({"evidenceId": evidence_id}),
                    observed_at,
                ),
            )
            cursor.execute(
                """
                UPDATE voice_call_current
                SET recording_state = 'revoked', recording_revoked_at = %s,
                    last_event_id = %s, updated_at = %s
                WHERE tenant_id = %s AND call_sid = %s
                """.strip(),
                (observed_at, event_id, observed_at, self._tenant_id, call_sid),
            )

    def get_current(self, call_sid: str) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            self._set_tenant(cursor)
            cursor.execute(
                """
                SELECT call_sid,provider_account_ref,from_number,to_number,lifecycle_state,
                       ai_disclosure_state,recording_state,recording_consent_evidence_id,
                       recording_consent_at,recording_refusal_at,recording_revoked_at,
                       last_event_id,updated_at
                FROM voice_call_current
                WHERE tenant_id = %s AND call_sid = %s
                """.strip(),
                (self._tenant_id, call_sid),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        keys = (
            "callSid",
            "providerAccountRef",
            "from",
            "to",
            "lifecycleState",
            "aiDisclosureState",
            "recordingState",
            "recordingConsentEvidenceId",
            "recordingConsentAt",
            "recordingRefusalAt",
            "recordingRevokedAt",
            "lastEventId",
            "updatedAt",
        )
        return dict(zip(keys, row, strict=True))

    def _set_tenant(self, cursor: Any) -> None:
        cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))


def _lifecycle_state(call_status: str) -> str:
    return {
        "ringing": "received",
        "queued": "received",
        "in-progress": "connected",
        "completed": "completed",
        "busy": "failed",
        "failed": "failed",
        "no-answer": "failed",
        "canceled": "failed",
    }.get(call_status, "received")
