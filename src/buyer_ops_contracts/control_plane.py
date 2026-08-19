"""HTTP control plane for Habitat, canonical, operator surface, ingress, activation, telemetry."""

from __future__ import annotations

import base64
import json
import os
import secrets
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .activation import (
    ActivationController,
    Ed25519ActivationDecisionSignatureVerifier,
    PostgresCapabilityDisablementVerifier,
)
from .actor_authorization import ActorTenantAuthorizationRepository
from .canonical_habitat import CanonicalLockedHabitatStateReader, PlatformPolicyEvaluator
from .canonical_repository import CanonicalRepository
from .capture import CaptureIncomplete
from .connector_service import ConnectorDenied, ConnectorGateway
from .errors import ContractViolation
from .habitat import HabitatKernel, HabitatState
from .habitat_repository import PostgresHabitatRepository, PostgresVersionLockedStateReader
from .ingress import IngressRejected
from .ingress_service import IngressService
from .operator_commands import OperatorCommandError, OperatorCommandService
from .operator_projection import OperatorProjection
from .release_evidence import ReleaseEvidenceEvaluator, load_gate_registry
from .telemetry import TelemetryRecorder


def connect(dsn: str) -> Any:
    return psycopg.connect(dsn)


class _LockedStateOnly:
    """Prevent accidental Habitat admission outside the PostgreSQL lock-owning repository."""

    def load_current(self, intent: dict[str, Any]) -> HabitatState:
        raise RuntimeError("Habitat state must be loaded under the repository transaction lock")


class ControlPlane:
    def __init__(
        self,
        dsn: str,
        *,
        permit_secret: bytes,
        control_token: str,
        release_public_keys: dict[str, Ed25519PublicKey],
        gate_registry_path: Path,
    ) -> None:
        if len(permit_secret) < 32:
            raise ValueError("permit_secret must contain at least 32 bytes")
        if not control_token:
            raise ValueError("control_token is required")
        if not release_public_keys:
            raise ValueError("at least one release decision public key is required")
        self._dsn = dsn
        self._permit_secret = permit_secret
        self._control_token = control_token
        self._release_public_keys = release_public_keys
        self._gate_registry, self._gate_registry_digest = load_gate_registry(gate_registry_path)

    def handle(
        self, method: str, path: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, Any]]:
        if headers.get("x-buyer-ops-token") != self._control_token:
            return 401, _error("authentication_required", "control token required")
        parsed = urlparse(path)
        route = parsed.path.rstrip("/") or "/"
        payload = json.loads(body.decode()) if body else {}
        tenant_id = headers.get("x-buyer-ops-tenant", "")
        actor_id = headers.get("x-buyer-ops-actor", "")
        try:
            if method == "GET" and route == "/health":
                return 200, {"status": "ok"}
            if method == "GET" and route == "/v1/actors/tenancies":
                return 200, {"tenancies": self._tenancies(actor_id)}
            if not tenant_id:
                return 403, _error("authority_denied", "tenant header required")
            if method == "GET" and route == "/v1/journeys":
                return 200, {"journeys": self._list_journeys(tenant_id, actor_id)}
            if method == "GET" and route.startswith("/v1/journeys/"):
                journey_id = route.split("/", 3)[-1]
                return 200, self._journey(tenant_id, actor_id, journey_id)
            if method == "POST" and route == "/v1/commands":
                return 200, self._command(tenant_id, actor_id, payload)
            if method == "POST" and route == "/v1/habitat/evaluate-authority":
                return 200, self._evaluate(tenant_id, payload)
            if method == "POST" and route == "/v1/habitat/admit-event":
                return 200, self._admit(tenant_id, payload)
            if method == "POST" and route == "/v1/ingress":
                return 200, self._ingress(tenant_id, payload)
            if method == "POST" and route == "/v1/ingress/envelope":
                return 200, self._ingress_envelope(tenant_id, payload)
            if method == "GET" and route == "/v1/connectors":
                return 200, {"connectors": self._connectors(tenant_id)}
            if method == "POST" and route == "/v1/connectors/invoke":
                return 200, self._invoke(tenant_id, payload, headers.get("x-buyer-ops-permit", ""))
            if method == "GET" and route == "/v1/activation":
                return 200, {"decisions": self._activation(tenant_id)}
            if method == "POST" and route == "/v1/activation/evidence":
                return 200, self._gate_evidence(tenant_id, payload)
            if method == "POST" and route == "/v1/activation/decisions":
                return 200, self._activation_decision(tenant_id, payload)
            if method == "POST" and route == "/v1/telemetry/observations":
                return 200, self._telemetry(tenant_id, payload)
            if method == "GET" and route.startswith("/v1/canonical/"):
                record_id = route.split("/", 3)[-1]
                return 200, self._canonical_get(tenant_id, record_id)
            if method == "POST" and route == "/v1/canonical":
                return 200, self._canonical_save(tenant_id, payload)
            if method == "POST" and route == "/v1/actor-authorizations":
                return 200, self._admit_actor_authorization(tenant_id, payload)
            return 404, _error("validation_failed", "unknown route")
        except KeyError as exc:
            return 404, _error("evidence_unavailable", str(exc))
        except OperatorCommandError as exc:
            status = 409 if exc.code in {"version_conflict", "payload_mismatch"} else 403
            if exc.code == "authentication_required":
                status = 401
            return status, _operator_error(exc, tenant_id)
        except CaptureIncomplete as exc:
            return 422, _error(exc.code, exc.detail)
        except IngressRejected as exc:
            return 409 if exc.code == "reconciliation_required" else 403, _error(exc.code, str(exc))
        except ConnectorDenied as exc:
            return 403, _error(exc.code, exc.detail)
        except ContractViolation as exc:
            return 422, {
                "code": "validation_failed",
                "violations": [
                    {"code": v.code, "path": v.path, "message": v.message} for v in exc.violations
                ],
            }
        except ValueError as exc:
            return 422, _error("validation_failed", str(exc))

    def _connection(self) -> Any:
        return connect(self._dsn)

    def _tenancies(self, actor_id: str) -> list[dict[str, str]]:
        if not actor_id:
            return []
        connection = self._connection()
        try:
            grants = ActorTenantAuthorizationRepository(connection).list_current_for_actor(actor_id)
        finally:
            connection.close()
        return [
            {"tenant_id": str(item["tenantId"]), "authorization_id": str(item["recordId"])}
            for item in grants
        ]

    def _admit_actor_authorization(self, tenant_id: str, record: dict[str, Any]) -> dict[str, Any]:
        connection = self._connection()
        try:
            return ActorTenantAuthorizationRepository(connection, tenant_id=tenant_id).save(record)
        finally:
            connection.close()

    def _list_journeys(self, tenant_id: str, actor_id: str) -> list[dict[str, Any]]:
        self._require_actor(tenant_id, actor_id)
        connection = self._connection()
        try:
            repo = CanonicalRepository(connection, tenant_id=tenant_id)
            projection = OperatorProjection(repo, tenant_id=tenant_id)
            views = []
            for journey_id in projection.list_journey_ids():
                views.append(projection.journey_view(journey_id=journey_id, principal_id=actor_id))
            return views
        finally:
            connection.close()

    def _journey(self, tenant_id: str, actor_id: str, journey_id: str) -> dict[str, Any]:
        self._require_actor(tenant_id, actor_id)
        connection = self._connection()
        try:
            repo = CanonicalRepository(connection, tenant_id=tenant_id)
            return OperatorProjection(repo, tenant_id=tenant_id).journey_view(
                journey_id=journey_id, principal_id=actor_id
            )
        finally:
            connection.close()

    def _command(self, tenant_id: str, actor_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_actor(tenant_id, actor_id)
        connection = self._connection()
        try:
            repo = CanonicalRepository(connection, tenant_id=tenant_id)
            service = OperatorCommandService(connection, repo, tenant_id=tenant_id)
            return service.dispatch(payload, actor_id=actor_id)
        finally:
            connection.close()

    def _evaluate(self, tenant_id: str, intent: dict[str, Any]) -> dict[str, Any]:
        connection = self._connection()
        try:
            reader = CanonicalLockedHabitatStateReader()
            kernel = HabitatKernel(_LockedStateOnly(), PlatformPolicyEvaluator())
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
                state = PostgresVersionLockedStateReader(reader).load_current(cursor, intent)
            connection.commit()
            decision = kernel.evaluate_current(
                intent,
                state=state,
                expected_tenant_id=tenant_id,
                evaluated_at=datetime.now(UTC),
            )
            return {
                "allowed": decision.allowed,
                "reason": decision.reason,
                "authoritative_versions": dict(decision.authoritative_versions),
                "policy_id": decision.policy_id,
                "policy_version": decision.policy_version,
            }
        finally:
            connection.close()

    def _admit(self, tenant_id: str, intent: dict[str, Any]) -> dict[str, Any]:
        connection = self._connection()
        try:
            reader = CanonicalLockedHabitatStateReader()
            kernel = HabitatKernel(_LockedStateOnly(), PlatformPolicyEvaluator())
            repo = PostgresHabitatRepository(
                connection,
                tenant_id=tenant_id,
                kernel=kernel,
                state_reader=PostgresVersionLockedStateReader(reader),
                permit_secret=self._permit_secret,
            )
            registration = repo.admit_and_register(intent, evaluated_at=datetime.now(UTC))
            permit = None
            if registration.permit is not None:
                permit = {
                    "permit_digest": registration.permit.permit_digest,
                    "state": registration.permit.state,
                    "expires_at": registration.permit.expires_at.isoformat(),
                }
            return {
                "allowed": registration.decision.allowed,
                "reason": registration.decision.reason,
                "permit": permit,
                "attempt": registration.attempt,
            }
        finally:
            connection.close()

    def _ingress(self, tenant_id: str, message: dict[str, Any]) -> dict[str, Any]:
        connection = self._connection()
        try:
            return IngressService(connection, tenant_id=tenant_id).admit(message)
        finally:
            connection.close()

    def _ingress_envelope(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        connection = self._connection()
        try:
            return IngressService(connection, tenant_id=tenant_id).admit_envelope(payload)
        finally:
            connection.close()

    def _connectors(self, tenant_id: str) -> list[dict[str, Any]]:
        connection = self._connection()
        try:
            repo = CanonicalRepository(connection, tenant_id=tenant_id)
            activation = self._activation_controller(connection, tenant_id)
            return ConnectorGateway(repo, activation, tenant_id=tenant_id).inventory()
        finally:
            connection.close()

    def _invoke(self, tenant_id: str, request: dict[str, Any], permit: str) -> dict[str, Any]:
        connection = self._connection()
        try:
            repo = CanonicalRepository(connection, tenant_id=tenant_id)
            activation = self._activation_controller(connection, tenant_id)
            return ConnectorGateway(repo, activation, tenant_id=tenant_id).invoke(
                request, permit_digest=permit
            )
        finally:
            connection.close()

    def _activation(self, tenant_id: str) -> list[dict[str, Any]]:
        connection = self._connection()
        try:
            return self._activation_controller(connection, tenant_id).list_decisions()
        finally:
            connection.close()

    def _gate_evidence(self, tenant_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
        connection = self._connection()
        try:
            return self._activation_controller(connection, tenant_id).record_gate_evidence(evidence)
        finally:
            connection.close()

    def _activation_decision(self, tenant_id: str, decision: dict[str, Any]) -> dict[str, Any]:
        connection = self._connection()
        try:
            return self._activation_controller(connection, tenant_id).record_decision(decision)
        finally:
            connection.close()

    def _telemetry(self, tenant_id: str, observation: dict[str, Any]) -> dict[str, Any]:
        connection = self._connection()
        try:
            return TelemetryRecorder(connection, tenant_id=tenant_id).record_observation(
                observation
            )
        finally:
            connection.close()

    def _canonical_get(self, tenant_id: str, record_id: str) -> dict[str, Any]:
        connection = self._connection()
        try:
            record = CanonicalRepository(connection, tenant_id=tenant_id).get(record_id)
            if record is None:
                raise KeyError(record_id)
            return record
        finally:
            connection.close()

    def _canonical_save(self, tenant_id: str, record: dict[str, Any]) -> dict[str, Any]:
        connection = self._connection()
        try:
            return CanonicalRepository(connection, tenant_id=tenant_id).save(record)
        finally:
            connection.close()

    def _activation_controller(self, connection: Any, tenant_id: str) -> ActivationController:
        disablement = PostgresCapabilityDisablementVerifier(connection, tenant_id=tenant_id)
        evaluator = ReleaseEvidenceEvaluator(
            self._gate_registry, self._gate_registry_digest, disablement
        )
        return ActivationController(
            connection,
            tenant_id=tenant_id,
            evaluator=evaluator,
            signature_verifier=Ed25519ActivationDecisionSignatureVerifier(
                self._release_public_keys
            ),
        )

    def _require_actor(self, tenant_id: str, actor_id: str) -> None:
        if not actor_id:
            raise OperatorCommandError(
                "authentication_required", retryable=False, detail="actor required"
            )
        tenancies = {item["tenant_id"] for item in self._tenancies(actor_id)}
        if tenant_id not in tenancies:
            raise OperatorCommandError(
                "authority_denied", retryable=False, detail="no tenant authorization"
            )


def _error(code: str, detail: str) -> dict[str, Any]:
    return {"code": code, "detail": detail}


def _operator_error(exc: OperatorCommandError, tenant_id: str) -> dict[str, Any]:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "message_type": "operator_error",
        "schema_version": "operator-surface/1.1.0",
        "error_id": f"err-{secrets.token_hex(8)}",
        "tenant_id": tenant_id or "unknown",
        "code": exc.code,
        "retryable": exc.retryable,
        "occurred_at": now,
        "correlation_id": f"corr-{secrets.token_hex(8)}",
        "safe_detail": exc.detail,
    }


class _Handler(BaseHTTPRequestHandler):
    plane: ControlPlane

    def log_message(self, format: str, *args: object) -> None:
        return

    def _headers(self) -> dict[str, str]:
        return {key.lower(): value for key, value in self.headers.items()}

    def _read(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length) if length else b""

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        status, payload = self.plane.handle("GET", self.path, self._headers(), b"")
        self._send(status, payload)

    def do_POST(self) -> None:
        status, payload = self.plane.handle("POST", self.path, self._headers(), self._read())
        self._send(status, payload)


def serve(host: str, port: int, plane: ControlPlane) -> ThreadingHTTPServer:
    handler = type("Handler", (_Handler,), {"plane": plane})
    server = ThreadingHTTPServer((host, port), handler)
    return server


def main() -> int:
    dsn = os.environ.get("BUYER_OPS_DATABASE_DSN") or os.environ.get("DATABASE_URL")
    token = os.environ.get("BUYER_OPS_CONTROL_TOKEN")
    secret = os.environ.get("BUYER_OPS_PERMIT_SECRET", "")
    registry_path = os.environ.get("BUYER_OPS_GATE_REGISTRY_PATH")
    release_keys_json = os.environ.get("BUYER_OPS_RELEASE_PUBLIC_KEYS_JSON")
    if (
        not dsn
        or not token
        or len(secret.encode()) < 32
        or not registry_path
        or not release_keys_json
    ):
        raise SystemExit(
            "BUYER_OPS_DATABASE_DSN, BUYER_OPS_CONTROL_TOKEN, BUYER_OPS_PERMIT_SECRET "
            "(>=32 bytes), BUYER_OPS_GATE_REGISTRY_PATH, and "
            "BUYER_OPS_RELEASE_PUBLIC_KEYS_JSON are required"
        )
    release_public_keys = _parse_release_public_keys(release_keys_json)
    host = os.environ.get("BUYER_OPS_CONTROL_HOST", "0.0.0.0")
    port = int(os.environ.get("BUYER_OPS_CONTROL_PORT", "8090"))
    plane = ControlPlane(
        dsn,
        permit_secret=secret.encode(),
        control_token=token,
        release_public_keys=release_public_keys,
        gate_registry_path=Path(registry_path),
    )
    server = serve(host, port, plane)
    print(f"buyer-ops control plane listening on {host}:{port}", flush=True)
    server.serve_forever()
    return 0


def _parse_release_public_keys(value: str) -> dict[str, Ed25519PublicKey]:
    encoded_keys = json.loads(value)
    if not isinstance(encoded_keys, dict) or not encoded_keys:
        raise ValueError("release public keys must be a non-empty JSON object")
    keys: dict[str, Ed25519PublicKey] = {}
    for key_id, encoded in encoded_keys.items():
        if not isinstance(key_id, str) or not key_id or not isinstance(encoded, str):
            raise ValueError("release public key entries must map key IDs to base64url strings")
        padding = "=" * (-len(encoded) % 4)
        try:
            raw = base64.urlsafe_b64decode(encoded + padding)
            keys[key_id] = Ed25519PublicKey.from_public_bytes(raw)
        except ValueError as exc:
            raise ValueError(f"invalid Ed25519 release public key: {key_id}") from exc
    return keys


if __name__ == "__main__":
    raise SystemExit(main())
