"""PostgreSQL linearization boundary for DW2-C1 effect admission."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .habitat import (
    HabitatDecision,
    HabitatKernel,
    HabitatState,
    validate_effect_intent,
)
from .semantic import validate_semantics
from .structural import validate_record


class Cursor(Protocol):
    def __enter__(self) -> Cursor: ...

    def __exit__(self, *args: object) -> None: ...

    def execute(self, statement: str, parameters: tuple[object, ...]) -> Any: ...

    def fetchone(self) -> tuple[object, ...] | None: ...

    def fetchall(self) -> list[tuple[object, ...]]: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class LockedHabitatStateReader(Protocol):
    """Load authoritative state using the transaction that owns the resource lock."""

    def load_current(self, cursor: Cursor, intent: dict[str, Any]) -> HabitatState: ...


class PostgresVersionLockedStateReader:
    """Replace caller-supplied versions with current canonical rows locked for the transaction."""

    def __init__(self, authority_reader: LockedHabitatStateReader) -> None:
        self._authority_reader = authority_reader

    def load_current(self, cursor: Cursor, intent: dict[str, Any]) -> HabitatState:
        record_ids = sorted(intent["canonical_version_vector"])
        cursor.execute(
            "SELECT record FROM canonical_records_current "
            "WHERE tenant_id = %s AND record_id = ANY(%s) ORDER BY record_id FOR UPDATE",
            (intent["tenant_id"], record_ids),
        )
        rows = cursor.fetchall()
        records: dict[str, dict[str, Any]] = {}
        for row in rows:
            record = row[0]
            if not isinstance(record, dict):
                raise RuntimeError("canonical record query returned a non-object value")
            records[str(record["id"])] = record
        missing = set(record_ids) - records.keys()
        if missing:
            # Missing authoritative resources remain a version conflict, never caller-owned truth.
            records.update({record_id: {"version": 0} for record_id in missing})
        authority_state = self._authority_reader.load_current(cursor, intent)
        return replace(authority_state, records=records)


@dataclass(frozen=True, slots=True)
class RedeemedEffectPermit:
    permit_digest: str
    intent_id: str
    tenant_id: str
    principal_id: str
    action_class: str
    connector_binding_id: str
    target_resource_type: str
    target_resource_id: str
    recipient_type: str
    recipient_id: str
    payload_digest: str
    idempotency_key: str
    canonical_version_vector: dict[str, int]
    issued_at: datetime
    expires_at: datetime
    redeemed_at: datetime
    state: str = "redeemed"


@dataclass(frozen=True, slots=True)
class HabitatRegistration:
    decision: HabitatDecision
    permit: RedeemedEffectPermit | None = None
    attempt: dict[str, Any] | None = None


class PostgresHabitatRepository:
    """Linearize current-state admission, permit redemption, and attempt creation."""

    def __init__(
        self,
        connection: Connection,
        *,
        tenant_id: str,
        kernel: HabitatKernel,
        state_reader: LockedHabitatStateReader,
        permit_secret: bytes,
        token_factory: Callable[[], bytes] | None = None,
        permit_ttl: timedelta = timedelta(seconds=30),
    ) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if len(permit_secret) < 32:
            raise ValueError("permit_secret must contain at least 32 bytes")
        if permit_ttl <= timedelta(0):
            raise ValueError("permit_ttl must be positive")
        self._connection = connection
        self._tenant_id = tenant_id
        self._kernel = kernel
        self._state_reader = state_reader
        self._permit_secret = permit_secret
        self._token_factory = token_factory or (lambda: secrets.token_bytes(32))
        self._permit_ttl = permit_ttl

    def admit_and_register(
        self, intent: dict[str, Any], *, evaluated_at: datetime
    ) -> HabitatRegistration:
        """Redeem internally; never return an unredeemed bearer permit."""
        validate_effect_intent(
            intent,
            expected_tenant_id=self._tenant_id,
            evaluated_at=evaluated_at,
        )
        now = evaluated_at.astimezone(UTC)
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                self._lock_effect(cursor, intent)
                ordering_token = self._ordering_token(cursor)
                if self._already_registered(cursor, intent):
                    decision = HabitatDecision(False, "permit_replayed")
                    self._record_decision(
                        cursor, intent, HabitatState(records={}), decision, ordering_token, now
                    )
                    self._connection.commit()
                    return HabitatRegistration(decision)

                state = self._state_reader.load_current(cursor, intent)
                decision = self._kernel.evaluate_current(
                    intent,
                    state=state,
                    expected_tenant_id=self._tenant_id,
                    evaluated_at=now,
                )
                if not decision.allowed:
                    self._record_decision(cursor, intent, state, decision, ordering_token, now)
                    self._connection.commit()
                    return HabitatRegistration(decision)

                permit = self._redeem_permit(cursor, intent, now)
                attempt = self._register_attempt(cursor, intent, permit, now)
                self._record_decision(
                    cursor,
                    intent,
                    state,
                    decision,
                    ordering_token,
                    now,
                    permit.permit_digest,
                )
            self._connection.commit()
            return HabitatRegistration(decision, permit, attempt)
        except Exception:
            self._connection.rollback()
            raise

    def _set_tenant(self, cursor: Cursor) -> None:
        cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))

    def _lock_effect(self, cursor: Cursor, intent: dict[str, Any]) -> None:
        resource = intent["target_resource"]
        keys = sorted(
            (
                f"resource:{self._tenant_id}:{resource['resource_type']}:{resource['resource_id']}",
                f"idempotency:{self._tenant_id}:{intent['idempotency_key']}",
            )
        )
        for key in keys:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))

    def _ordering_token(self, cursor: Cursor) -> int:
        cursor.execute("SELECT txid_current()", ())
        row = cursor.fetchone()
        if row is None or not isinstance(row[0], int):
            raise RuntimeError("PostgreSQL did not return a transaction ordering token")
        return row[0]

    def _already_registered(self, cursor: Cursor, intent: dict[str, Any]) -> bool:
        cursor.execute(
            "SELECT 1 FROM habitat_effect_permits "
            "WHERE tenant_id = %s AND (intent_id = %s OR idempotency_key = %s)",
            (self._tenant_id, intent["intent_id"], intent["idempotency_key"]),
        )
        return cursor.fetchone() is not None

    def _redeem_permit(
        self, cursor: Cursor, intent: dict[str, Any], now: datetime
    ) -> RedeemedEffectPermit:
        token = self._token_factory()
        digest = "sha256:" + hmac.new(self._permit_secret, token, hashlib.sha256).hexdigest()
        proposal_expiry = datetime.fromisoformat(
            intent["proposal_expires_at"].replace("Z", "+00:00")
        ).astimezone(UTC)
        expires_at = min(proposal_expiry, now + self._permit_ttl)
        target = intent["target_resource"]
        recipient = intent["recipient"]
        permit = RedeemedEffectPermit(
            permit_digest=digest,
            intent_id=intent["intent_id"],
            tenant_id=self._tenant_id,
            principal_id=intent["principal_id"],
            action_class=intent["action_class"],
            connector_binding_id=intent["connector_binding_id"],
            target_resource_type=target["resource_type"],
            target_resource_id=target["resource_id"],
            recipient_type=recipient["recipient_type"],
            recipient_id=recipient["recipient_id"],
            payload_digest=intent["payload_digest"],
            idempotency_key=intent["idempotency_key"],
            canonical_version_vector=dict(intent["canonical_version_vector"]),
            issued_at=now,
            expires_at=expires_at,
            redeemed_at=now,
        )
        cursor.execute(
            """INSERT INTO habitat_effect_permits (
                tenant_id, permit_digest, intent_id, principal_id, action_class,
                connector_binding_id, target_resource_type, target_resource_id,
                recipient_type, recipient_id, payload_digest, idempotency_key,
                canonical_version_vector, issued_at, expires_at, redeemed_at, state
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      %s::jsonb, %s, %s, %s, 'redeemed')""",
            (
                permit.tenant_id,
                permit.permit_digest,
                permit.intent_id,
                permit.principal_id,
                permit.action_class,
                permit.connector_binding_id,
                permit.target_resource_type,
                permit.target_resource_id,
                permit.recipient_type,
                permit.recipient_id,
                permit.payload_digest,
                permit.idempotency_key,
                json.dumps(permit.canonical_version_vector, sort_keys=True),
                permit.issued_at,
                permit.expires_at,
                permit.redeemed_at,
            ),
        )
        return permit

    def _register_attempt(
        self,
        cursor: Cursor,
        intent: dict[str, Any],
        permit: RedeemedEffectPermit,
        now: datetime,
    ) -> dict[str, Any]:
        timestamp = now.isoformat().replace("+00:00", "Z")
        attempt = {
            "id": f"effect-attempt:{intent['intent_id']}",
            "tenantId": self._tenant_id,
            "schemaVersion": "buyer-ops/0.3.0",
            "recordType": "EffectAttempt",
            "version": 1,
            "createdAt": timestamp,
            "updatedAt": timestamp,
            "effectiveFrom": timestamp,
            "createdBy": {
                "actorType": "service_principal",
                "actorId": intent["principal_id"],
            },
            "sourceEvidenceIds": list(intent["evidence_correlation_ids"]),
            "status": "active",
            "intentId": intent["intent_id"],
            "actionClass": intent["action_class"],
            "payloadDigest": intent["payload_digest"],
            "permitDigest": permit.permit_digest,
            "idempotencyKey": intent["idempotency_key"],
            "attemptState": "registered",
        }
        validate_record(attempt, "ontology")
        validate_semantics(attempt)
        encoded = json.dumps(attempt, sort_keys=True, separators=(",", ":"))
        parameters: tuple[object, ...] = (
            self._tenant_id,
            attempt["id"],
            1,
            "EffectAttempt",
            "buyer-ops/0.3.0",
            encoded,
        )
        cursor.execute(
            """INSERT INTO canonical_records_current
                (tenant_id, record_id, version, record_type, schema_version, record)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)""",
            parameters,
        )
        cursor.execute(
            """INSERT INTO canonical_record_versions
                (tenant_id, record_id, version, record_type, schema_version, record)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)""",
            parameters,
        )
        return attempt

    def _record_decision(
        self,
        cursor: Cursor,
        intent: dict[str, Any],
        state: HabitatState,
        decision: HabitatDecision,
        ordering_token: int,
        now: datetime,
        permit_digest: str | None = None,
    ) -> None:
        decision_id = f"{intent['intent_id']}:{ordering_token}:{decision.reason}"
        cursor.execute(
            """INSERT INTO habitat_authority_decisions (
                tenant_id, decision_id, intent_id, trace_id, ordering_token, decided_at,
                decision, reason, policy_id, policy_version, intent, authoritative_state,
                authoritative_versions, permit_digest
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      %s::jsonb, %s::jsonb, %s::jsonb, %s)""",
            (
                self._tenant_id,
                decision_id,
                intent["intent_id"],
                intent["trace_id"],
                ordering_token,
                now,
                "allowed" if decision.allowed else "denied",
                decision.reason,
                decision.policy_id,
                decision.policy_version,
                json.dumps(intent, sort_keys=True),
                json.dumps(asdict(state), sort_keys=True),
                json.dumps(dict(decision.authoritative_versions), sort_keys=True),
                permit_digest,
            ),
        )
