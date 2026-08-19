"""Release-activation storage: signed GateEvidence and ActivationDecision."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any, Protocol

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from psycopg.types.json import Jsonb

from .canonical_repository import Connection
from .closure_repository import PostgresClosureRepository
from .digest import sha256_digest
from .release_evidence import (
    ReleaseEvidenceEvaluator,
    evaluate_accessibility_evidence,
)
from .structural import validate_record


class ActivationDecisionSignatureVerifier(Protocol):
    def verify(self, decision: dict[str, Any]) -> bool: ...


class Ed25519ActivationDecisionSignatureVerifier:
    def __init__(self, public_keys: dict[str, Ed25519PublicKey]) -> None:
        self._public_keys = public_keys

    def verify(self, decision: dict[str, Any]) -> bool:
        signature = decision.get("signature")
        if not isinstance(signature, dict) or signature.get("algorithm") != "ed25519":
            return False
        key = self._public_keys.get(str(signature.get("keyId", "")))
        encoded = signature.get("value")
        if key is None or not isinstance(encoded, str):
            return False
        unsigned = dict(decision)
        unsigned.pop("signature", None)
        try:
            padding = "=" * (-len(encoded) % 4)
            key.verify(base64.urlsafe_b64decode(encoded + padding), rfc8785.dumps(unsigned))
        except (InvalidSignature, ValueError):
            return False
        return True


class PostgresCapabilityDisablementVerifier:
    """Accept only a cited, latest signed activation decision that deactivates the capability."""

    def __init__(self, connection: Connection, *, tenant_id: str) -> None:
        self._connection = connection
        self._tenant_id = tenant_id

    def proves_disabled(self, capability_id: str, evidence_refs: list[str]) -> bool:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
            cursor.execute(
                """
                SELECT payload FROM release_activation_decisions
                WHERE tenant_id = %s AND capability_id = %s
                ORDER BY decided_at DESC
                LIMIT 1
                """.strip(),
                (self._tenant_id, capability_id),
            )
            row = cursor.fetchone()
        if row is None or not isinstance(row[0], dict):
            return False
        decision = row[0]
        return (
            decision.get("decision") == "deactivate" and decision.get("decisionId") in evidence_refs
        )


class ActivationController:
    """Persist and read back release-activation/1.0.0 records. Never self-asserts pass."""

    def __init__(
        self,
        connection: Connection,
        *,
        tenant_id: str,
        evaluator: ReleaseEvidenceEvaluator | None = None,
        signature_verifier: ActivationDecisionSignatureVerifier | None = None,
    ) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._connection = connection
        self._tenant_id = tenant_id
        self._evaluator = evaluator
        self._signature_verifier = signature_verifier

    def record_gate_evidence(self, evidence: dict[str, Any]) -> dict[str, Any]:
        if evidence.get("recordType") not in {"ReleaseEvidence", "AccessibilityEvidence"}:
            raise ValueError("legacy gate evidence is audit-only and cannot satisfy activation")
        return PostgresClosureRepository(self._connection, tenant_id=self._tenant_id).save(evidence)

    def record_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        validate_record(decision, "release_activation")
        if decision.get("messageType") != "activation_decision":
            raise ValueError("messageType must be activation_decision")
        if decision["tenantId"] != self._tenant_id:
            raise ValueError("activation decision tenant does not match repository tenant")
        if self._signature_verifier is None or not self._signature_verifier.verify(decision):
            raise ValueError("activation decision signature is invalid")
        self._validate_decision_evidence(decision)
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    INSERT INTO release_activation_decisions (
                        tenant_id, decision_id, capability_id, payload, decided_at
                    ) VALUES (%s, %s, %s, %s, %s)
                    """.strip(),
                    (
                        self._tenant_id,
                        decision["decisionId"],
                        decision["capabilityId"],
                        Jsonb(decision),
                        decision["decidedAt"],
                    ),
                )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return decision

    def current_decision(self, capability_id: str) -> dict[str, Any] | None:
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    SELECT payload FROM release_activation_decisions
                    WHERE tenant_id = %s AND capability_id = %s
                    ORDER BY decided_at DESC
                    LIMIT 1
                    """.strip(),
                    (self._tenant_id, capability_id),
                )
                row = cursor.fetchone()
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        if row is None:
            return None
        payload = row[0]
        return payload if isinstance(payload, dict) else None

    def capability_activated(self, capability_id: str) -> bool:
        decision = self.current_decision(capability_id)
        if decision is None or decision.get("decision") != "activate":
            return False
        try:
            self._validate_decision_evidence(decision, evaluated_at=datetime.now(UTC))
        except (ValueError, RuntimeError):
            return False
        return True

    def list_decisions(self) -> list[dict[str, Any]]:
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    SELECT DISTINCT ON (capability_id) payload
                    FROM release_activation_decisions
                    WHERE tenant_id = %s
                    ORDER BY capability_id, decided_at DESC
                    """.strip(),
                    (self._tenant_id,),
                )
                rows = cursor.fetchall()
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return [row[0] for row in rows if isinstance(row[0], dict)]

    def _validate_decision_evidence(
        self, decision: dict[str, Any], *, evaluated_at: datetime | None = None
    ) -> None:
        if self._evaluator is None:
            raise ValueError("release evidence evaluator is required")
        if (
            decision["gateRegistryVersion"] != self._evaluator.registry_version
            or decision["gateRegistryDigest"] != self._evaluator.registry_digest
        ):
            raise ValueError("activation decision gate registry binding mismatch")
        required = self._evaluator.required_gate_ids(decision["directlyApplicableGateIds"])
        if tuple(sorted(decision["requiredGateIds"])) != required:
            raise ValueError("activation required gate set mismatch")
        all_ids = [*decision["evidenceIds"], *decision["accessibilityEvidenceIds"]]
        records = self._load_current_evidence(all_ids)
        if len(records) != len(set(all_ids)):
            raise ValueError("activation evidence is missing or ambiguous")
        by_type: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            by_type.setdefault(record["recordType"], []).append(record)
        now = evaluated_at or datetime.fromisoformat(
            decision["decidedAt"].replace("Z", "+00:00")
        ).astimezone(UTC)
        gate_evidence_ids = self._evaluator.evaluate(
            release_digest=decision["releaseDigest"],
            directly_applicable_gate_ids=decision["directlyApplicableGateIds"],
            evidence=by_type.get("ReleaseEvidence", []),
            now=now,
        )
        if tuple(sorted(decision["evidenceIds"])) != tuple(sorted(gate_evidence_ids)):
            raise ValueError("activation gate evidence set mismatch")
        accessibility_ids = evaluate_accessibility_evidence(
            by_type.get("AccessibilityEvidence", []),
            release_digest=decision["releaseDigest"],
            deployed_builds=decision["deployedBuildDigests"],
            now=now,
        )
        if tuple(sorted(decision["accessibilityEvidenceIds"])) != tuple(sorted(accessibility_ids)):
            raise ValueError("activation accessibility evidence set mismatch")
        if decision["evidenceSetDigest"] != evidence_set_digest(sorted(all_ids)):
            raise ValueError("activation evidence set digest mismatch")

    def _load_current_evidence(self, evidence_ids: list[str]) -> list[dict[str, Any]]:
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    SELECT payload FROM closure_records_current
                    WHERE tenant_id = %s AND record_id = ANY(%s)
                    """.strip(),
                    (self._tenant_id, evidence_ids),
                )
                rows = cursor.fetchall()
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return [row[0] for row in rows if isinstance(row[0], dict)]

    def _set_tenant(self, cursor: Any) -> None:
        cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))


def evidence_set_digest(evidence_ids: list[str]) -> str:
    return sha256_digest(sorted(evidence_ids))
