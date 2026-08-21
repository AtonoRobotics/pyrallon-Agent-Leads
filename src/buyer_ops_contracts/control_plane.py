"""HTTP control plane for Habitat, canonical, operator surface, ingress, activation, telemetry."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import mimetypes
import os
import secrets
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

import psycopg
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .activation import (
    ActivationController,
    Ed25519ActivationDecisionSignatureVerifier,
    PostgresCapabilityDisablementVerifier,
)
from .actor_authorization import ActorTenantAuthorizationRepository, admit_published_record
from .calendar_operations import CalendarOperationService, ConnectorCalendarProvider
from .canonical_habitat import CanonicalLockedHabitatStateReader, PlatformPolicyEvaluator
from .canonical_repository import CanonicalRepository, Connection
from .capture import CaptureIncomplete
from .cognition_authorization import CognitionAuthorization
from .cognitive_credentials_runtime import PostgresCognitiveCredentialResolver
from .cognitive_service import CognitiveRuntimeService, configuration_from_environment
from .connector_authorization import (
    ConnectorAuthorization,
    PlatformOAuthStore,
    oauth_clients_from_env,
    parse_oauth_state,
)
from .connector_gateway import ConnectorRejected
from .connector_runtime import (
    ConnectorRuntimeError,
    PostgresConnectorRuntime,
    configured_adapters_from_environment,
)
from .connector_service import ConnectorDenied, ConnectorGateway
from .derived_contract_repository import (
    BookingOutcomeRepository,
    DerivedContractReader,
    SlotSetRepository,
)
from .errors import ContractViolation, SetupRejected
from .esignature_operations import ConnectorESignatureProvider, ESignatureOperationService
from .esignature_repository import ESignatureOperationRepository
from .habitat import HabitatKernel, HabitatState
from .habitat_repository import PostgresHabitatRepository, PostgresVersionLockedStateReader
from .ingress import IngressRejected
from .ingress_runtime import ConfiguredIngressError, ConfiguredIngressRuntimeFactory
from .ingress_service import IngressProviderRuntime, IngressService
from .operator_commands import OperatorCommandError, OperatorCommandService
from .operator_policy import OperatorPolicyRepository
from .operator_projection import JourneyViewDerivationPolicy, OperatorProjection
from .release_evidence import ReleaseEvidenceEvaluator, load_gate_registry
from .telemetry import TelemetryRecorder
from .voice_repository import VoiceCallRepository

_JOURNEY_VIEW_CATEGORIES = {
    "identity",
    "representation",
    "consent",
    "connector",
    "cognition",
    "workflow",
    "calendar",
    "policy",
    "authority",
    "evidence",
}
_JOURNEY_VIEW_RECOVERY_OWNERS = {
    "system",
    "agent",
    "brokerage",
    "deployment_operator",
    "buyer",
}


def connect(dsn: str) -> Any:
    return psycopg.connect(dsn)


@contextmanager
def _database_context(dsn: str) -> Any:
    connection = connect(dsn)
    try:
        yield connection
    finally:
        connection.close()


class _UnavailableCalendarProvider:
    """Sentinel provider for the availability-only operation."""

    def book(self, command: dict[str, Any], slot: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("calendar provider is unavailable for availability derivation")

    def reschedule(self, command: dict[str, Any], slot: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("calendar provider is unavailable for availability derivation")

    def cancel(self, command: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("calendar provider is unavailable for availability derivation")

    def reconcile(self, prior_result: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("calendar provider is unavailable for availability derivation")

    def snapshot(
        self, binding: dict[str, Any], *, range_start: str, range_end: str
    ) -> dict[str, Any]:
        raise RuntimeError("calendar provider is unavailable for availability derivation")


class _PostgresCalendarInvoker:
    def __init__(self, runtime: PostgresConnectorRuntime) -> None:
        self._runtime = runtime

    def __call__(
        self,
        request: dict[str, Any],
        payload: bytes,
        *,
        permit_digest: str,
        preview: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return self._runtime.invoke(
            request,
            payload,
            permit_digest=permit_digest,
            preview=preview,
        )

    def reconcile(
        self,
        request: dict[str, Any],
        provider_receipt_id: str,
        *,
        permit_digest: str,
    ) -> dict[str, Any]:
        del permit_digest
        return self._runtime.reconcile(request, provider_receipt_id)


def _calendar_records(
    payload: dict[str, Any], *, include_availability: bool = True, include_booking: bool = False
) -> dict[str, dict[str, Any]]:
    required = {"binding": "binding"}
    if include_availability:
        required.update(
            {
                "policy": "policy",
                "readiness": "readiness",
                "snapshot": "snapshot",
            }
        )
    records: dict[str, dict[str, Any]] = {}
    for field, label in required.items():
        value = payload.get(field)
        if not isinstance(value, dict):
            raise ValueError(f"{label} record is required")
        records[field] = value
    if payload.get("command") is not None:
        command = payload.get("command")
        if not isinstance(command, dict):
            raise ValueError("command record must be an object")
        records["command"] = command
    if include_booking:
        if "command" not in records:
            raise ValueError("command record is required")
        prior_result = payload.get("priorResult")
        if not isinstance(prior_result, dict):
            raise ValueError("priorResult record is required")
        records["priorResult"] = prior_result
    if payload.get("slotSet") is not None:
        slot_set = payload.get("slotSet")
        if not isinstance(slot_set, dict):
            raise ValueError("slotSet record must be an object")
        records["slotSet"] = slot_set
    return records


def _connector_capability(provider_action: str) -> str:
    """Map provider verbs to the governed connector capability vocabulary."""
    if provider_action in {"calendar.book", "esign.create"}:
        return "create"
    if provider_action in {"calendar.reschedule", "calendar.cancel", "esign.void"}:
        return "update"
    return "read"


def _published_calendar_records(
    connection_factory: Callable[[], Any],
    tenant_id: str,
    records: dict[str, dict[str, Any]],
    *,
    fields: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    identity_fields = {
        "availability_policy": "policyId",
        "calendar_provider_binding": "bindingId",
        "calendar_snapshot": "snapshotId",
        "slot_set": "slotSetId",
        "booking_command": "commandId",
        "booking_result": "resultId",
        "booking_reconciliation": "reconciliationId",
        "readiness_decision": "decisionId",
    }
    reader = DerivedContractReader(connection_factory, tenant_id=tenant_id)
    authoritative = dict(records)
    for field in fields:
        record = records.get(field)
        if not isinstance(record, dict):
            raise ValueError(f"{field} record is required")
        message_type = record.get("messageType")
        if not isinstance(message_type, str) or message_type not in identity_fields:
            raise ValueError(f"{field} record has an unsupported message type")
        family = (
            "qualification_readiness"
            if message_type == "readiness_decision"
            else "availability_booking"
        )
        identity_field = identity_fields[message_type]
        record_id = record.get(identity_field)
        if not isinstance(record_id, str) or not record_id:
            raise ValueError(f"{field} record identity is required")
        version = record.get("version", 1)
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError(f"{field} record version is invalid")
        stored = reader.get(
            contract_family=family,
            message_type=message_type,
            record_id=record_id,
            record_version=version,
        )
        if stored is None:
            raise ValueError(f"{field} record is not published")
        authoritative[field] = stored
    return authoritative


class _LockedStateOnly:
    """Prevent accidental Habitat admission outside the PostgreSQL lock-owning repository."""

    def load_current(self, intent: dict[str, Any]) -> HabitatState:
        raise RuntimeError("Habitat state must be loaded under the repository transaction lock")


class IngressProviderRuntimeFactory(Protocol):
    def __call__(self, *, connection: Connection, tenant_id: str) -> IngressProviderRuntime: ...


def load_journey_view_policy(raw: str | None = None) -> JourneyViewDerivationPolicy:
    """Load deployment-owned JourneyView bindings without supplying runtime defaults."""
    encoded = raw if raw is not None else os.environ.get("BUYER_OPS_JOURNEY_VIEW_POLICY_JSON", "")
    if not encoded.strip():
        raise ValueError("BUYER_OPS_JOURNEY_VIEW_POLICY_JSON is required")
    payload = json.loads(encoded)
    if not isinstance(payload, dict):
        raise ValueError("JourneyView policy must be an object")
    compiler_version = payload.get("compiler_version")
    bindings = payload.get("blocker_bindings")
    if not isinstance(compiler_version, str) or not compiler_version:
        raise ValueError("JourneyView policy compiler_version is required")
    if not isinstance(bindings, dict) or not bindings:
        raise ValueError("JourneyView policy blocker_bindings are required")
    normalized: dict[str, tuple[str, str]] = {}
    for code, binding in bindings.items():
        if (
            not isinstance(code, str)
            or not code
            or not isinstance(binding, list)
            or len(binding) != 2
            or not all(isinstance(value, str) and value for value in binding)
            or binding[0] not in _JOURNEY_VIEW_CATEGORIES
            or binding[1] not in _JOURNEY_VIEW_RECOVERY_OWNERS
        ):
            raise ValueError(
                "JourneyView policy bindings must map codes to [category, recovery_owner]"
            )
        normalized[code] = (binding[0], binding[1])
    return JourneyViewDerivationPolicy(
        compiler_version=compiler_version,
        blocker_bindings=normalized,
    )


class ControlPlane:
    def __init__(
        self,
        dsn: str,
        *,
        permit_secret: bytes,
        control_token: str,
        release_public_keys: dict[str, Ed25519PublicKey],
        gate_registry_path: Path,
        ingress_provider_runtime_factory: IngressProviderRuntimeFactory | None = None,
        ingress_webhook_factory: ConfiguredIngressRuntimeFactory | None = None,
        capability_inventory_verifier: Callable[[dict[str, Any]], bool] | None = None,
        journey_view_policy: JourneyViewDerivationPolicy | None = None,
        connector_adapters: dict[str, Any] | None = None,
        cognitive_runtime: CognitiveRuntimeService | None = None,
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
        self._capability_inventory_verifier = capability_inventory_verifier
        self._activation_verifier = Ed25519ActivationDecisionSignatureVerifier(release_public_keys)
        self._journey_view_policy = journey_view_policy
        self._connector_adapters = connector_adapters
        self._cognitive_runtime = cognitive_runtime
        self._gate_registry, self._gate_registry_digest = load_gate_registry(gate_registry_path)
        self._oauth_clients = oauth_clients_from_env()
        self._ingress_provider_runtime_factory = ingress_provider_runtime_factory
        self._ingress_webhook_factory = ingress_webhook_factory

    def handle(
        self, method: str, path: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, Any]]:
        parsed = urlparse(path)
        route = parsed.path.rstrip("/") or "/"
        if route.startswith("/v1/ingress/webhook/"):
            provider_id = unquote(route.removeprefix("/v1/ingress/webhook/")).strip("/")
            if method != "POST":
                return 405, _error("validation_failed", "webhook endpoint only accepts POST")
            if self._ingress_webhook_factory is None:
                return 422, _error(
                    "configuration_incomplete",
                    "configured ingress webhook adapters are unavailable",
                )
            connection = self._connection()
            try:
                return 200, self._ingress_webhook_factory.handle_webhook(
                    connection, provider_id, headers, body
                )
            except ConfiguredIngressError as exc:
                code = (
                    "ingress_authentication_failed"
                    if "signature" in str(exc).lower()
                    else "validation_failed"
                )
                return (403 if code == "ingress_authentication_failed" else 422), _error(
                    code, str(exc)
                )
            finally:
                connection.close()
        if headers.get("x-buyer-ops-token") != self._control_token:
            return 401, _error("authentication_required", "control token required")
        tenant_id = headers.get("x-buyer-ops-tenant", "")
        actor_id = headers.get("x-buyer-ops-actor", "")
        try:
            payload = json.loads(body.decode()) if body else {}
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            if route == "/v1/commands":
                error = OperatorCommandError(
                    "validation_failed", retryable=False, detail="invalid JSON request body"
                )
                return 422, _operator_error(error, tenant_id)
            return 422, _error("validation_failed", str(exc))
        try:
            if method == "GET" and route == "/health":
                return 200, {"status": "ok"}
            if method == "GET" and route == "/v1/actors/tenancies":
                if not actor_id:
                    return 401, _error("authentication_required", "actor required")
                return 200, {"tenancies": self._tenancies(actor_id)}
            if method == "POST" and route == "/v1/setup/tenant":
                if not actor_id:
                    return 401, _error("authentication_required", "actor required")
                return 422, _error(
                    "configuration_incomplete",
                    "governed tenant bootstrap semantics are not published",
                )
            if method == "POST" and route == "/v1/connectors/oauth/complete":
                if not actor_id:
                    return 401, _error("authentication_required", "actor required")
                return 200, self._oauth_complete(actor_id, payload)
            if method == "GET" and route == "/v1/platform/oauth-clients":
                if not actor_id:
                    return 401, _error("authentication_required", "actor required")
                public = os.environ.get("OPERATOR_PUBLIC_URL", "").strip().rstrip("/")
                return 200, {
                    "clients": self._platform_oauth_clients(),
                    "publicOrigin": public,
                    "redirectUri": f"{public}/api/connectors/callback" if public else "",
                }
            if method == "POST" and route == "/v1/platform/oauth-clients":
                if not actor_id:
                    return 401, _error("authentication_required", "actor required")
                if not self._tenancies(actor_id):
                    return 403, _error(
                        "authority_denied",
                        "admit brokerage identity before registering this application's OAuth clients",
                    )
                return 200, self._save_platform_oauth_client(actor_id, payload)
            if not tenant_id:
                return 403, _error("authority_denied", "tenant header required")
            if method == "GET" and route == "/v1/journeys":
                self._require_actor(tenant_id, actor_id)
                if self._journey_view_policy is None:
                    return 422, _error(
                        "configuration_incomplete",
                        "governed operator projection rules are not published",
                    )
                return 200, self._journeys(tenant_id, actor_id)
            if method == "GET" and route.startswith("/v1/journeys/"):
                self._require_actor(tenant_id, actor_id)
                if self._journey_view_policy is None:
                    return 422, _error(
                        "configuration_incomplete",
                        "governed operator projection rules are not published",
                    )
                journey_id = unquote(route.removeprefix("/v1/journeys/")).strip("/")
                return 200, self._journeys(tenant_id, actor_id, journey_id=journey_id)
            if method == "GET" and route == "/v1/workspace":
                self._require_actor(tenant_id, actor_id)
                if self._journey_view_policy is None:
                    return 422, _error(
                        "configuration_incomplete",
                        "governed operator projection rules are not published",
                    )
                return 200, self._journeys(tenant_id, actor_id)
            if method == "POST" and route == "/v1/cognition/invoke":
                self._require_actor(tenant_id, actor_id)
                if self._cognitive_runtime is None:
                    return 422, _error(
                        "configuration_incomplete",
                        "governed cognitive runtime is not configured",
                    )
                work_request = payload.get("workRequest")
                if not isinstance(work_request, dict):
                    return 422, _error("validation_failed", "workRequest must be an object")
                if work_request.get("tenantId") != tenant_id:
                    return 403, _error("blocked_policy", "work request tenant mismatch")
                return 200, self._cognitive_runtime.invoke(work_request)
            if method == "GET" and route.startswith("/v1/workspace/journeys/"):
                self._require_actor(tenant_id, actor_id)
                if self._journey_view_policy is None:
                    return 422, _error(
                        "configuration_incomplete",
                        "governed operator projection rules are not published",
                    )
                journey_id = unquote(route.removeprefix("/v1/workspace/journeys/")).strip("/")
                return 200, self._journeys(tenant_id, actor_id, journey_id=journey_id)
            if method == "POST" and route == "/v1/workspace/appointments":
                return 200, self._command(tenant_id, actor_id, payload)
            if method == "POST" and route == "/v1/calendar/availability":
                self._require_actor(tenant_id, actor_id)
                return 200, self._calendar_availability(tenant_id, payload)
            if method == "POST" and route == "/v1/calendar/snapshot":
                self._require_actor(tenant_id, actor_id)
                return 200, self._calendar_snapshot(
                    tenant_id, payload, headers.get("x-buyer-ops-permit", "")
                )
            if method == "POST" and route == "/v1/calendar/booking":
                self._require_actor(tenant_id, actor_id)
                return 200, self._calendar_booking(
                    tenant_id, payload, headers.get("x-buyer-ops-permit", "")
                )
            if method == "POST" and route == "/v1/calendar/reconcile":
                self._require_actor(tenant_id, actor_id)
                return 200, self._calendar_reconcile(
                    tenant_id, payload, headers.get("x-buyer-ops-permit", "")
                )
            if method == "POST" and route == "/v1/representation/esign/present":
                self._require_actor(tenant_id, actor_id)
                return 200, self._esign_present(
                    tenant_id, payload, headers.get("x-buyer-ops-permit", "")
                )
            if method == "POST" and route == "/v1/representation/esign/reconcile":
                self._require_actor(tenant_id, actor_id)
                return 200, self._esign_reconcile(
                    tenant_id, payload, headers.get("x-buyer-ops-permit", "")
                )
            if route.startswith("/v1/voice/calls/"):
                self._require_actor(tenant_id, actor_id)
                voice_route = route.removeprefix("/v1/voice/calls/").strip("/")
                if voice_route.endswith("/recording-consent") and method == "POST":
                    call_sid = unquote(voice_route.removesuffix("/recording-consent")).strip("/")
                    return 200, self._voice_recording_consent(tenant_id, call_sid, payload)
                if voice_route.endswith("/recording-revoke") and method == "POST":
                    call_sid = unquote(voice_route.removesuffix("/recording-revoke")).strip("/")
                    return 200, self._voice_recording_revoke(tenant_id, call_sid, payload)
                if method == "GET":
                    return 200, self._voice_current(tenant_id, unquote(voice_route))
                return 405, _error("validation_failed", "unsupported voice call operation")
            if method == "POST" and route == "/v1/workspace/assertions":
                return 200, self._command(tenant_id, actor_id, payload)
            if method == "POST" and route == "/v1/workspace/suppressions":
                return 200, self._command(tenant_id, actor_id, payload)
            if method == "POST" and route == "/v1/commands":
                return 200, self._command(tenant_id, actor_id, payload)
            if method == "POST" and route == "/v1/habitat/evaluate-authority":
                self._require_actor(tenant_id, actor_id)
                return 200, self._evaluate(tenant_id, payload)
            if method == "POST" and route == "/v1/habitat/admit-event":
                self._require_actor(tenant_id, actor_id)
                return 200, self._admit(tenant_id, payload)
            if method == "POST" and route == "/v1/ingress":
                return 200, self._ingress(tenant_id, payload)
            if method == "POST" and route == "/v1/ingress/envelope":
                return 200, self._ingress_envelope(tenant_id, payload)
            if method == "GET" and route == "/v1/connectors":
                self._require_actor(tenant_id, actor_id)
                return 200, {"connectors": self._connectors(tenant_id)}
            if method == "POST" and route == "/v1/connectors/oauth/start":
                self._require_actor(tenant_id, actor_id)
                return 200, self._oauth_start(tenant_id, actor_id, payload)
            if method == "GET" and route == "/v1/cognition/identities":
                self._require_actor(tenant_id, actor_id)
                return 200, {"identities": self._cognition_identities(tenant_id, actor_id)}
            if method == "POST" and route == "/v1/cognition/oauth/start":
                self._require_actor(tenant_id, actor_id)
                return 200, self._cognition_oauth_start(tenant_id, actor_id, payload)
            if method == "POST" and route == "/v1/cognition/oauth/poll":
                self._require_actor(tenant_id, actor_id)
                return 200, self._cognition_oauth_poll(tenant_id, actor_id, payload)
            if method == "POST" and route == "/v1/cognition/metered":
                self._require_actor(tenant_id, actor_id)
                return 200, self._cognition_metered(tenant_id, actor_id, payload)
            if method == "POST" and route == "/v1/cognition/local":
                self._require_actor(tenant_id, actor_id)
                return 200, self._cognition_local(tenant_id, actor_id, payload)
            if method == "POST" and route == "/v1/connectors/invoke":
                return 200, self._invoke(tenant_id, payload, headers.get("x-buyer-ops-permit", ""))
            if method == "GET" and route == "/v1/activation":
                self._require_actor(tenant_id, actor_id)
                return 200, {"decisions": self._activation(tenant_id)}
            if method == "POST" and route == "/v1/activation/evidence":
                self._require_actor(tenant_id, actor_id)
                return 200, self._gate_evidence(tenant_id, payload)
            if method == "POST" and route == "/v1/activation/decisions":
                self._require_actor(tenant_id, actor_id)
                return 200, self._activation_decision(tenant_id, payload)
            if method == "POST" and route == "/v1/telemetry/observations":
                return 200, self._telemetry(tenant_id, payload)
            if method == "GET" and route.startswith("/v1/canonical/"):
                self._require_actor(tenant_id, actor_id)
                record_id = unquote(route.split("/", 3)[-1])
                return 200, self._canonical_get(tenant_id, record_id)
            if method == "POST" and route == "/v1/canonical":
                self._require_actor(tenant_id, actor_id)
                return 422, _error(
                    "configuration_incomplete",
                    "canonical mutation must use a published command boundary",
                )
            if method == "POST" and route == "/v1/actor-authorizations":
                self._require_actor(tenant_id, actor_id)
                return 200, self._admit_actor_authorization(tenant_id, actor_id, payload)
            if method == "POST" and route == "/v1/operator-policies":
                self._require_actor(tenant_id, actor_id)
                return 200, self._admit_operator_policy(tenant_id, actor_id, payload)
            return 404, _error("validation_failed", "unknown route")
        except KeyError as exc:
            return 404, _error("evidence_unavailable", str(exc))
        except OperatorCommandError as exc:
            status = 409 if exc.code in {"version_conflict", "payload_mismatch"} else 403
            if exc.code == "authentication_required":
                status = 401
            elif exc.code in {"validation_failed", "configuration_incomplete"}:
                status = 422
            return status, _operator_error(exc, tenant_id)
        except CaptureIncomplete as exc:
            return 422, _error(exc.code, exc.detail)
        except SetupRejected as exc:
            status = 403 if exc.code in {"authority_denied", "policy_denied"} else 422
            return status, _error(exc.code, exc.detail)
        except IngressRejected as exc:
            status = 409 if exc.code == "reconciliation_required" else 403
            if exc.code == "configuration_incomplete":
                status = 422
            return status, _error(exc.code, str(exc))
        except ConnectorDenied as exc:
            status = {
                "configuration_incomplete": 422,
                "validation_failed": 422,
                "version_conflict": 409,
            }.get(exc.code, 403)
            return status, _error(exc.code, exc.detail)
        except ConnectorRejected as exc:
            status = {
                "connector_unavailable": 503,
                "capability_inventory_required": 422,
                "capability_inventory_signature_invalid": 403,
                "effect_draft_preview_required": 422,
                "effect_permit_required": 403,
                "permit_mismatch": 403,
                "connector_response_mismatch": 502,
            }.get(exc.code, 403)
            return status, _error(exc.code, str(exc))
        except ConnectorRuntimeError as exc:
            status = (
                503 if exc.code in {"connector_unavailable", "provider_response_invalid"} else 502
            )
            return status, _error(exc.code, exc.detail)
        except PermissionError as exc:
            return 403, _error("authority_denied", str(exc))
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

    def _tenancies(self, actor_id: str) -> list[dict[str, Any]]:
        if not actor_id:
            return []
        connection = self._connection()
        try:
            grants = ActorTenantAuthorizationRepository(connection).list_current_for_actor(actor_id)
            return [_tenancy_projection(item, connection) for item in grants]
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

    def _voice_current(self, tenant_id: str, call_sid: str) -> dict[str, Any]:
        if not call_sid:
            raise ValueError("call sid is required")
        connection = self._connection()
        try:
            current = VoiceCallRepository(connection, tenant_id=tenant_id).get_current(call_sid)
            if current is None:
                raise KeyError(call_sid)
            return {"call": current}
        finally:
            connection.close()

    def _voice_recording_consent(
        self, tenant_id: str, call_sid: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        affirmative = payload.get("affirmative")
        evidence_id = payload.get("evidenceId")
        event_id = payload.get("eventId")
        if not isinstance(affirmative, bool) or not all(
            isinstance(value, str) and value for value in (evidence_id, event_id)
        ):
            raise ValueError("affirmative, evidenceId, and eventId are required")
        evidence_id_value = str(evidence_id)
        event_id_value = str(event_id)
        connection = self._connection()
        try:
            state = VoiceCallRepository(connection, tenant_id=tenant_id).set_recording_consent(
                call_sid=call_sid,
                evidence_id=evidence_id_value,
                affirmative=affirmative,
                observed_at=_voice_observed_at(payload),
                event_id=event_id_value,
            )
            connection.commit()
            return {"callSid": call_sid, "recordingState": state}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _voice_recording_revoke(
        self, tenant_id: str, call_sid: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        evidence_id = payload.get("evidenceId")
        event_id = payload.get("eventId")
        if not all(isinstance(value, str) and value for value in (evidence_id, event_id)):
            raise ValueError("evidenceId and eventId are required")
        evidence_id_value = str(evidence_id)
        event_id_value = str(event_id)
        connection = self._connection()
        try:
            VoiceCallRepository(connection, tenant_id=tenant_id).revoke_recording(
                call_sid=call_sid,
                evidence_id=evidence_id_value,
                observed_at=_voice_observed_at(payload),
                event_id=event_id_value,
            )
            connection.commit()
            return {"callSid": call_sid, "recordingState": "revoked"}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _calendar_availability(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        records = _calendar_records(payload)

        def connection_factory() -> Any:
            return _database_context(self._dsn)

        records = _published_calendar_records(
            connection_factory,
            tenant_id,
            records,
            fields=("policy", "readiness", "binding", "snapshot"),
        )
        slot_sets = SlotSetRepository(
            connection_factory,
            tenant_id=tenant_id,
        )
        # Availability has no provider effect; the service's provider is used only by booking.
        service = CalendarOperationService(
            _UnavailableCalendarProvider(),
            slot_sets=slot_sets,
            outcomes=BookingOutcomeRepository(connection_factory, tenant_id=tenant_id),
        )
        locations = payload.get("locationOptions")
        if not isinstance(locations, list):
            raise ValueError("locationOptions must be a list")
        normalized_locations: list[tuple[str, tuple[str, ...]]] = []
        for item in locations:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("locationId"), str)
                or not isinstance(item.get("resourceIds"), list)
                or not all(isinstance(value, str) for value in item["resourceIds"])
            ):
                raise ValueError("locationOptions entries are invalid")
            normalized_locations.append((item["locationId"], tuple(item["resourceIds"])))
        return {
            "slotSet": service.availability(
                policy=records["policy"],
                readiness=records["readiness"],
                binding=records["binding"],
                snapshot=records["snapshot"],
                principal_id=str(payload.get("principalId") or ""),
                location_options=tuple(normalized_locations),
                blocked_intervals=tuple(payload.get("blockedIntervals") or ()),
            )
        }

    def _calendar_booking(
        self, tenant_id: str, payload: dict[str, Any], permit_digest: str
    ) -> dict[str, Any]:
        if not permit_digest:
            raise ConnectorRejected("effect_permit_required")
        records = _calendar_records(payload, include_availability=False)
        request = payload.get("request")
        if not isinstance(request, dict):
            raise ValueError("request is required")
        request = dict(request)
        if request.get("tenantId") != tenant_id:
            raise PermissionError("calendar connector request tenant mismatch")

        def published_connection_factory() -> Any:
            return _database_context(self._dsn)

        records = _published_calendar_records(
            published_connection_factory,
            tenant_id,
            records,
            fields=tuple(field for field in ("binding", "slotSet", "snapshot") if field in records),
        )
        connection = self._connection()
        try:
            runtime = PostgresConnectorRuntime(
                connection,
                tenant_id=tenant_id,
                activation=self._activation_controller(connection, tenant_id),
                adapters=self._connector_adapters or {},
                permit_secret=self._permit_secret,
            )
            provider = ConnectorCalendarProvider(
                _PostgresCalendarInvoker(runtime),
                request_for=lambda _record, action, raw: {
                    **request,
                    "capability": _connector_capability(action),
                    "payloadDigest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                },
                permit_digest=permit_digest,
            )

            def connection_factory() -> Any:
                return _database_context(self._dsn)

            service = CalendarOperationService(
                provider,
                slot_sets=SlotSetRepository(connection_factory, tenant_id=tenant_id),
                outcomes=BookingOutcomeRepository(connection_factory, tenant_id=tenant_id),
            )
            result = service.booking(
                command=records["command"],
                binding=records["binding"],
                slot_set=records.get("slotSet"),
                current_snapshot=records.get("snapshot"),
                current_provider_watermark=str(payload.get("currentProviderWatermark") or ""),
                current_appointment_version=(
                    int(payload["currentAppointmentVersion"])
                    if payload.get("currentAppointmentVersion") is not None
                    else None
                ),
                # The permit is issued only by Habitat admission and is required by the runtime.
                authority_active=True,
            )
            return {"bookingResult": result}
        finally:
            connection.close()

    def _calendar_snapshot(
        self, tenant_id: str, payload: dict[str, Any], permit_digest: str
    ) -> dict[str, Any]:
        if not permit_digest:
            raise ConnectorRejected("effect_permit_required")
        records = _calendar_records(payload, include_availability=False)
        request = payload.get("request")
        range_start = payload.get("rangeStart")
        range_end = payload.get("rangeEnd")
        if (
            not isinstance(request, dict)
            or request.get("tenantId") != tenant_id
            or not isinstance(range_start, str)
            or not isinstance(range_end, str)
        ):
            raise ValueError("tenant-scoped request and snapshot range are required")

        def published_connection_factory() -> Any:
            return _database_context(self._dsn)

        records = _published_calendar_records(
            published_connection_factory, tenant_id, records, fields=("binding",)
        )
        connection = self._connection()
        try:
            runtime = PostgresConnectorRuntime(
                connection,
                tenant_id=tenant_id,
                activation=self._activation_controller(connection, tenant_id),
                adapters=self._connector_adapters or {},
                permit_secret=self._permit_secret,
            )
            provider = ConnectorCalendarProvider(
                _PostgresCalendarInvoker(runtime),
                request_for=lambda _source, action, raw: {
                    **request,
                    "capability": _connector_capability(action),
                    "payloadDigest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                },
                permit_digest=permit_digest,
            )

            def factory() -> Any:
                return _database_context(self._dsn)

            service = CalendarOperationService(
                provider,
                slot_sets=SlotSetRepository(factory, tenant_id=tenant_id),
                outcomes=BookingOutcomeRepository(factory, tenant_id=tenant_id),
            )
            return {
                "calendarSnapshot": service.snapshot(
                    binding=records["binding"],
                    range_start=range_start,
                    range_end=range_end,
                )
            }
        finally:
            connection.close()

    def _calendar_reconcile(
        self, tenant_id: str, payload: dict[str, Any], permit_digest: str
    ) -> dict[str, Any]:
        if not permit_digest:
            raise ConnectorRejected("effect_permit_required")
        records = _calendar_records(payload, include_availability=False, include_booking=True)
        request = payload.get("request")
        if not isinstance(request, dict):
            raise ValueError("request is required")
        if request.get("tenantId") != tenant_id:
            raise PermissionError("calendar connector request tenant mismatch")

        def published_connection_factory() -> Any:
            return _database_context(self._dsn)

        records = _published_calendar_records(
            published_connection_factory,
            tenant_id,
            records,
            fields=("command", "binding", "priorResult"),
        )
        connection = self._connection()
        try:
            runtime = PostgresConnectorRuntime(
                connection,
                tenant_id=tenant_id,
                activation=self._activation_controller(connection, tenant_id),
                adapters=self._connector_adapters or {},
                permit_secret=self._permit_secret,
            )
            provider = ConnectorCalendarProvider(
                _PostgresCalendarInvoker(runtime),
                request_for=lambda _record, action, raw: {
                    **request,
                    "capability": _connector_capability(action),
                    "providerAction": action,
                    "payloadDigest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                },
                permit_digest=permit_digest,
            )

            def connection_factory() -> Any:
                return _database_context(self._dsn)

            service = CalendarOperationService(
                provider,
                slot_sets=SlotSetRepository(connection_factory, tenant_id=tenant_id),
                outcomes=BookingOutcomeRepository(connection_factory, tenant_id=tenant_id),
            )
            reconciliation = service.reconciliation(
                command=records["command"],
                binding=records["binding"],
                prior_result=records["priorResult"],
            )
            return {"bookingReconciliation": reconciliation}
        finally:
            connection.close()

    def _esign_present(
        self, tenant_id: str, payload: dict[str, Any], permit_digest: str
    ) -> dict[str, Any]:
        request = payload.get("request")
        if not isinstance(request, dict) or request.get("tenantId") != tenant_id:
            raise ValueError("tenant-scoped connector request is required")
        connection = self._connection()
        try:
            agreement, approval = self._load_esign_inputs(connection, tenant_id, payload)
            request = dict(request)
            request["recipients"] = self._resolve_esign_recipients(connection, tenant_id, agreement)
            operations = ESignatureOperationRepository(connection, tenant_id=tenant_id)
            prior = operations.latest(agreement_id=str(agreement["id"]))
            if prior is not None and prior.get("state") == "presented":
                if agreement.get("executionState") == "agent_approved":
                    repaired = dict(agreement)
                    repaired["version"] = int(agreement["version"]) + 1
                    repaired["executionState"] = "presented"
                    repaired["updatedAt"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                    agreement = CanonicalRepository(connection, tenant_id=tenant_id).save(
                        repaired, expected_version=int(agreement["version"])
                    )
                return {"eSignature": prior, "duplicate": True}
            if not permit_digest:
                raise ConnectorRejected("effect_permit_required")
            runtime = PostgresConnectorRuntime(
                connection,
                tenant_id=tenant_id,
                activation=self._activation_controller(connection, tenant_id),
                adapters=self._connector_adapters or {},
                permit_secret=self._permit_secret,
            )
            provider = ConnectorESignatureProvider(
                _PostgresCalendarInvoker(runtime),
                request_for=lambda _source, action, raw: {
                    **request,
                    "capability": _connector_capability(action),
                    "providerAction": action,
                    "payloadDigest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                },
                permit_digest=permit_digest,
            )
            result = ESignatureOperationService(provider).present(
                agreement,
                agent_approved=True,
                approval_digest=str(approval["payloadDigest"]),
            )
            operation = {"tenantId": tenant_id, "agreementId": str(agreement["id"]), **result}
            operations.append(operation_id=f"esign:present:{agreement['id']}", record=operation)
            if agreement.get("executionState") == "agent_approved":
                presented = dict(agreement)
                presented["version"] = int(agreement["version"]) + 1
                presented["executionState"] = "presented"
                presented["updatedAt"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                CanonicalRepository(connection, tenant_id=tenant_id).save(
                    presented, expected_version=int(agreement["version"])
                )
            return {"eSignature": operation, "duplicate": False}
        finally:
            connection.close()

    def _esign_reconcile(
        self, tenant_id: str, payload: dict[str, Any], permit_digest: str
    ) -> dict[str, Any]:
        request = payload.get("request")
        envelope_id = payload.get("providerEnvelopeId")
        if (
            not isinstance(request, dict)
            or request.get("tenantId") != tenant_id
            or not isinstance(envelope_id, str)
            or not envelope_id
        ):
            raise ValueError("tenant-scoped request and providerEnvelopeId are required")
        connection = self._connection()
        try:
            agreement, _approval = self._load_esign_inputs(
                connection, tenant_id, payload, approval_required=False
            )
            prior_operation = ESignatureOperationRepository(connection, tenant_id=tenant_id).latest(
                agreement_id=str(agreement["id"])
            )
            if (
                prior_operation is not None
                and prior_operation.get("providerEnvelopeId") == envelope_id
                and prior_operation.get("state") == "completed"
            ):
                return {"eSignature": prior_operation, "duplicate": True}
            if not permit_digest:
                raise ConnectorRejected("effect_permit_required")
            if (
                agreement.get("executionState") == "agent_approved"
                and prior_operation
                and prior_operation.get("state") == "presented"
            ):
                repaired = dict(agreement)
                repaired["version"] = int(agreement["version"]) + 1
                repaired["executionState"] = "presented"
                repaired["updatedAt"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                agreement = CanonicalRepository(connection, tenant_id=tenant_id).save(
                    repaired, expected_version=int(agreement["version"])
                )
            if agreement.get("executionState") not in {"presented", "partially_signed"}:
                raise ValueError("agreement must be presented before provider reconciliation")
            runtime = PostgresConnectorRuntime(
                connection,
                tenant_id=tenant_id,
                activation=self._activation_controller(connection, tenant_id),
                adapters=self._connector_adapters or {},
                permit_secret=self._permit_secret,
            )
            provider = ConnectorESignatureProvider(
                _PostgresCalendarInvoker(runtime),
                request_for=lambda _source, action, raw: {
                    **request,
                    "capability": _connector_capability(action),
                    "providerAction": action,
                    "payloadDigest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                },
                permit_digest=permit_digest,
            )
            result = ESignatureOperationService(provider).reconcile(
                agreement,
                provider_envelope_id=envelope_id,
            )
            operation = {"tenantId": tenant_id, "agreementId": str(agreement["id"]), **result}
            completed_agreement = result.get("agreement")
            if result.get("state") == "completed" and isinstance(completed_agreement, dict):
                repository = CanonicalRepository(connection, tenant_id=tenant_id)
                executed = dict(completed_agreement)
                executed["version"] = int(agreement["version"]) + 1
                executed["executionState"] = "executed"
                executed["updatedAt"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                saved_executed = repository.save(
                    executed, expected_version=int(agreement["version"])
                )
                effective = dict(saved_executed)
                effective["version"] = int(saved_executed["version"]) + 1
                effective["executionState"] = "effective"
                effective["updatedAt"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                saved_effective = repository.save(
                    effective, expected_version=int(saved_executed["version"])
                )
                operation["agreement"] = saved_effective
            ESignatureOperationRepository(connection, tenant_id=tenant_id).append(
                operation_id=f"esign:reconcile:{agreement['id']}:{envelope_id}:{result['state']}",
                record=operation,
            )
            return {"eSignature": operation}
        finally:
            connection.close()

    @staticmethod
    def _load_esign_inputs(
        connection: Any,
        tenant_id: str,
        payload: dict[str, Any],
        *,
        approval_required: bool = True,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        repository = CanonicalRepository(connection, tenant_id=tenant_id)
        agreement_id = payload.get("agreementId")
        approval_id = payload.get("approvalId")
        if not isinstance(agreement_id, str) or not agreement_id:
            raise ValueError("agreementId is required")
        agreement = repository.get(agreement_id)
        if agreement is None or agreement.get("recordType") != "WrittenBuyerAgreement":
            raise ValueError("published WrittenBuyerAgreement is required")
        if approval_required:
            if not isinstance(approval_id, str) or not approval_id:
                raise ValueError("approvalId is required")
            approval = repository.get(approval_id)
            if (
                approval is None
                or approval.get("recordType") != "Approval"
                or approval.get("decision") != "approved"
                or approval.get("actionIntentId") != agreement_id
            ):
                raise PermissionError("published approval for agreement is required")
            return agreement, approval
        return agreement, {}

    @staticmethod
    def _resolve_esign_recipients(
        connection: Any, tenant_id: str, agreement: dict[str, Any]
    ) -> list[dict[str, str]]:
        """Resolve approved signer roles from canonical parties and contact endpoints."""
        party_ids = agreement.get("buyerPartyIds")
        if not isinstance(party_ids, list) or not party_ids:
            raise ValueError("e-signature agreement has no buyer party")
        repository = CanonicalRepository(connection, tenant_id=tenant_id)
        parties = {
            str(record.get("id")): record
            for record in repository.list_by_type("BuyingParty")
            if isinstance(record, dict)
        }
        persons = {
            str(record.get("id")): record
            for record in repository.list_by_type("Person")
            if isinstance(record, dict)
        }
        endpoints = [
            record
            for record in repository.list_by_type("ContactEndpoint")
            if isinstance(record, dict)
            and record.get("endpointType") == "email"
            and record.get("ownershipState") in {"asserted", "authorized"}
            and record.get("verificationState") in {"provider_observed", "verified"}
            and record.get("contactabilityState") == "contactable"
        ]
        by_owner: dict[str, list[dict[str, Any]]] = {}
        for endpoint in endpoints:
            by_owner.setdefault(str(endpoint.get("ownerId")), []).append(endpoint)
        roles = {
            "buyer": "Buyer",
            "co_buyer": "CoBuyer",
            "decision_participant": "DecisionParticipant",
            "observer": "Observer",
        }
        recipients: list[dict[str, str]] = []
        for party_id in party_ids:
            party = parties.get(str(party_id))
            if party is None:
                raise ValueError("e-signature buyer party is not canonical")
            members = party.get("members")
            if not isinstance(members, list) or not members:
                raise ValueError("e-signature buyer party has no members")
            for member in members:
                if not isinstance(member, dict):
                    raise ValueError("e-signature buyer party member is invalid")
                person_id = str(member.get("personId") or "")
                person = persons.get(person_id)
                choices = sorted(by_owner.get(person_id, []), key=lambda item: str(item.get("id")))
                if person is None or not choices:
                    raise ValueError("e-signature signer email endpoint is unavailable")
                email = str(choices[0]["normalizedValue"])
                recipients.append(
                    {
                        "roleName": roles.get(str(member.get("role")), "Buyer"),
                        "name": str(person.get("displayName") or "").strip(),
                        "email": email,
                    }
                )
        if not recipients or any(not item["name"] for item in recipients):
            raise ValueError("e-signature signer identity is incomplete")
        return recipients

    def _journeys(
        self, tenant_id: str, principal_id: str, *, journey_id: str | None = None
    ) -> dict[str, Any]:
        connection = self._connection()
        try:
            repository = CanonicalRepository(connection, tenant_id=tenant_id)
            projection = OperatorProjection(
                repository,
                tenant_id=tenant_id,
                derivation_policy=self._journey_view_policy,
            )
            snapshot = repository.current_records()
            if journey_id is not None:
                return projection.journey_view(
                    journey_id=journey_id, principal_id=principal_id, records=snapshot
                )
            return {
                "journeys": [
                    projection.journey_view(
                        journey_id=str(item["id"]), principal_id=principal_id, records=snapshot
                    )
                    for item in snapshot
                    if item.get("recordType") == "BuyerJourney" and isinstance(item.get("id"), str)
                ]
            }
        finally:
            connection.close()

    def _evaluate(self, tenant_id: str, intent: dict[str, Any]) -> dict[str, Any]:
        connection = self._connection()
        try:
            reader = CanonicalLockedHabitatStateReader(
                inventory_verifier=self._capability_inventory_verifier,
                activation_verifier=self._activation_verifier.verify,
            )
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
            reader = CanonicalLockedHabitatStateReader(
                inventory_verifier=self._capability_inventory_verifier,
                activation_verifier=self._activation_verifier.verify,
            )
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
            runtime = (
                self._ingress_provider_runtime_factory(
                    connection=connection,
                    tenant_id=tenant_id,
                )
                if self._ingress_provider_runtime_factory is not None
                else None
            )
            return IngressService(
                connection,
                tenant_id=tenant_id,
                provider_runtime=runtime,
            ).admit_envelope(payload)
        finally:
            connection.close()

    def _platform_oauth(self, connection: Any) -> PlatformOAuthStore:
        return PlatformOAuthStore(connection, permit_secret=self._permit_secret)

    def _platform_oauth_clients(self) -> list[dict[str, str]]:
        connection = self._connection()
        try:
            return self._platform_oauth(connection).list_public()
        finally:
            connection.close()

    def _cognition_auth(
        self, connection: Any, tenant_id: str, actor_id: str
    ) -> CognitionAuthorization:
        return CognitionAuthorization(
            connection,
            tenant_id=tenant_id,
            permit_secret=self._permit_secret,
            actor_id=actor_id,
        )

    def _cognition_identities(self, tenant_id: str, actor_id: str) -> list[dict[str, str]]:
        connection = self._connection()
        try:
            return self._cognition_auth(connection, tenant_id, actor_id).identities()
        finally:
            connection.close()

    def _cognition_oauth_start(
        self, tenant_id: str, actor_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        connection = self._connection()
        try:
            auth = self._cognition_auth(connection, tenant_id, actor_id)
            connector_id = str(payload.get("connectorId") or "")
            auth.refuse_unsupported(connector_id)
            if connector_id != "openai.chatgpt":
                raise SetupRejected(
                    "validation_failed", "only ChatGPT subscription uses device OAuth"
                )
            return auth.start_chatgpt_device()
        finally:
            connection.close()

    def _cognition_oauth_poll(
        self, tenant_id: str, actor_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        connection = self._connection()
        try:
            return self._cognition_auth(connection, tenant_id, actor_id).poll_chatgpt_device(
                str(payload.get("sessionId") or "")
            )
        finally:
            connection.close()

    def _cognition_metered(
        self, tenant_id: str, actor_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        connection = self._connection()
        try:
            auth = self._cognition_auth(connection, tenant_id, actor_id)
            connector_id = str(payload.get("connectorId") or "")
            auth.refuse_unsupported(connector_id)
            return auth.bind_metered(
                connector_id=connector_id,
                api_key=str(payload.get("apiKey") or ""),
            )
        finally:
            connection.close()

    def _cognition_local(
        self, tenant_id: str, actor_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        connection = self._connection()
        try:
            return self._cognition_auth(connection, tenant_id, actor_id).bind_local(
                base_url=str(payload.get("baseUrl") or ""),
                model_id=str(payload.get("modelId") or ""),
                token=str(payload.get("token") or ""),
            )
        finally:
            connection.close()

    def _connector_auth(self, connection: Any, tenant_id: str) -> ConnectorAuthorization:
        return ConnectorAuthorization(
            connection,
            tenant_id=tenant_id,
            permit_secret=self._permit_secret,
            oauth_clients=PlatformOAuthStore(
                connection, permit_secret=self._permit_secret
            ).material(),
        )

    def _save_platform_oauth_client(self, actor_id: str, payload: dict[str, Any]) -> dict[str, str]:
        del actor_id
        connection = self._connection()
        try:
            return PlatformOAuthStore(connection, permit_secret=self._permit_secret).save(
                issuer=str(payload.get("issuer") or ""),
                client_id=str(payload.get("clientId") or ""),
                client_secret=str(payload.get("clientSecret") or ""),
                directory_id=(
                    str(payload.get("directoryId"))
                    if payload.get("directoryId") is not None
                    else None
                ),
            )
        finally:
            connection.close()

    def _admit_actor_authorization(
        self, tenant_id: str, actor_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if payload.get("recordType") != "ActorTenantAuthorization":
            raise ValueError("recordType must be ActorTenantAuthorization")
        if payload.get("tenantId") != tenant_id or payload.get("actorId") != actor_id:
            raise PermissionError("actor authorization tenant or actor mismatch")
        connection = self._connection()
        try:
            return admit_published_record(connection, payload)
        finally:
            connection.close()

    def _admit_operator_policy(
        self, tenant_id: str, actor_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        del actor_id
        if payload.get("message_type") != "operator_policy":
            raise ValueError("message_type must be operator_policy")
        if payload.get("tenant_id") != tenant_id:
            raise PermissionError("operator policy tenant mismatch")
        connection = self._connection()
        try:
            return OperatorPolicyRepository(connection, tenant_id=tenant_id).admit(
                payload,
                expected_version=(
                    int(payload["expected_version"])
                    if payload.get("expected_version") is not None
                    else None
                ),
            )
        finally:
            connection.close()

    def _connectors(self, tenant_id: str) -> list[dict[str, Any]]:
        connection = self._connection()
        try:
            repo = CanonicalRepository(connection, tenant_id=tenant_id)
            rows = ConnectorGateway(repo, tenant_id=tenant_id).inventory()
            bindings = self._connector_auth(connection, tenant_id).bindings()
            for row in rows:
                bound = bindings.get(str(row.get("grant_id")), {})
                row["authorization"] = bound.get("authorization", "unbound")
                row["provider_account_ref"] = bound.get("provider_account_ref")
            return rows
        finally:
            connection.close()

    def _oauth_start(
        self, tenant_id: str, actor_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        connection = self._connection()
        try:
            return self._connector_auth(connection, tenant_id).start_oauth(
                actor_id=actor_id,
                connector_id=str(payload.get("connectorId") or ""),
                redirect_uri=str(payload.get("redirectUri") or ""),
                return_origin=str(payload.get("returnOrigin") or ""),
            )
        finally:
            connection.close()

    def _oauth_complete(self, actor_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        state = str(payload.get("state") or "")
        tenant_id, _session_id = parse_oauth_state(
            self._permit_secret, state, now=datetime.now(UTC)
        )
        del _session_id
        connection = self._connection()
        try:
            return self._connector_auth(connection, tenant_id).complete_oauth(
                code=str(payload.get("code") or ""),
                state=state,
                actor_id=actor_id,
                account_sid=str(payload.get("accountSid") or ""),
            )
        finally:
            connection.close()

    def _invoke(self, tenant_id: str, request: dict[str, Any], permit: str) -> dict[str, Any]:
        connection = self._connection()
        try:
            if self._connector_adapters is not None:
                encoded_payload = request.get("payloadBase64")
                governed_request = request.get("request")
                if (
                    not isinstance(governed_request, dict)
                    or not isinstance(encoded_payload, str)
                    or not encoded_payload
                ):
                    raise ConnectorDenied(
                        "validation_failed",
                        "configured connector runtime requires request and payloadBase64",
                    )
                try:
                    payload = base64.b64decode(encoded_payload, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise ConnectorDenied("validation_failed", "payloadBase64 is invalid") from exc
                response = PostgresConnectorRuntime(
                    connection,
                    tenant_id=tenant_id,
                    activation=self._activation_controller(connection, tenant_id),
                    adapters=self._connector_adapters,
                    permit_secret=self._permit_secret,
                ).invoke(
                    governed_request,
                    payload,
                    permit_digest=permit,
                    preview=request.get("preview")
                    if isinstance(request.get("preview"), dict)
                    else None,
                )
                return response
            repo = CanonicalRepository(connection, tenant_id=tenant_id)
            return ConnectorGateway(repo, tenant_id=tenant_id).invoke(request, permit_digest=permit)
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

    def _activation_controller(self, connection: Any, tenant_id: str) -> ActivationController:
        disablement = PostgresCapabilityDisablementVerifier(connection, tenant_id=tenant_id)
        evaluator = ReleaseEvidenceEvaluator(
            self._gate_registry,
            self._gate_registry_digest,
            disablement,
            tenant_id=tenant_id,
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


def _tenancy_projection(grant: dict[str, Any], connection: Any | None = None) -> dict[str, Any]:
    authorization_ref = {
        "record_id": str(grant["recordId"]),
        "record_type": "ActorTenantAuthorization",
        "version": int(grant["authorizationVersion"]),
        "status": str(grant["status"]),
    }
    policy_id = str(grant["policyVersion"])
    policy_ref: dict[str, Any] | None = None
    if connection is not None:
        from .operator_policy import OperatorPolicyRepository

        try:
            policy = OperatorPolicyRepository(
                connection, tenant_id=str(grant["tenantId"])
            ).get_current(policy_id)
        except Exception:
            policy = None
        if policy is not None:
            policy_ref = {
                "record_id": policy_id,
                "record_type": "OperatorPolicy",
                "version": int(policy["record_version"]),
                "status": str(policy.get("status") or "active"),
            }
    return {
        "tenant_id": str(grant["tenantId"]),
        "principal_id": str(grant["principalId"]),
        "role": str(grant["role"]),
        "authorization_id": str(grant["recordId"]),
        "authorization_version": int(grant["authorizationVersion"]),
        "authorization_ref": authorization_ref,
        "policy_version": policy_id,
        "policy_ref": policy_ref,
        "allowed_commands": list(grant["allowedCommands"]),
        "record_scopes": list(grant["recordScopes"]),
        "status": str(grant["status"]),
    }


def _error(code: str, detail: str) -> dict[str, Any]:
    return {"code": code, "detail": detail}


def _voice_observed_at(payload: dict[str, Any]) -> datetime:
    value = payload.get("observedAt")
    if value is None:
        return datetime.now(UTC)
    if not isinstance(value, str):
        raise ValueError("observedAt must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("observedAt must be an RFC 3339 timestamp") from exc
    if parsed.utcoffset() is None:
        raise ValueError("observedAt must include a timezone")
    return parsed.astimezone(UTC)


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


def _ui_asset(path: str) -> tuple[bytes, str] | None:
    """Resolve only the packaged UI shell; never expose arbitrary filesystem paths."""
    root = Path(os.environ.get("BUYER_OPS_UI_ROOT", Path(__file__).resolve().parents[2] / "ui"))
    root = root.resolve()
    route = urlparse(path).path
    if route in {"/", "/api/connectors/callback"}:
        relative = Path("index.html")
    elif route.startswith("/assets/"):
        relative = Path("assets") / unquote(route.removeprefix("/assets/"))
    else:
        return None
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
    return candidate.read_bytes(), content_type


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
        if payload.get("_httpContentType") == "application/xml":
            raw = str(payload.get("_httpBody") or "").encode()
            content_type = "application/xml; charset=utf-8"
        else:
            raw = json.dumps(payload).encode()
            content_type = "application/json"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_asset(self, asset: tuple[bytes, str]) -> None:
        raw, content_type = asset
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        asset = _ui_asset(self.path)
        if asset is not None:
            self._send_asset(asset)
            return
        if urlparse(self.path).path.startswith("/assets/"):
            self.send_error(404)
            return
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
    journey_view_policy = load_journey_view_policy()
    ingress_factory = ConfiguredIngressRuntimeFactory.from_environment()
    connector_adapters = configured_adapters_from_environment()
    cognitive_runtime = None
    cognitive_configured = all(
        os.environ.get(name, "").strip()
        for name in (
            "BUYER_OPS_COGNITIVE_ROUTE_POLICY_JSON",
            "BUYER_OPS_COGNITIVE_IDENTITIES_JSON",
            "BUYER_OPS_COGNITIVE_PROFILES_JSON",
            "BUYER_OPS_COGNITIVE_RUNTIMES_JSON",
        )
    )
    if cognitive_configured:
        credential_resolver = PostgresCognitiveCredentialResolver(
            dsn,
            permit_secret=secret.encode(),
        )
        cognitive_runtime = CognitiveRuntimeService(
            configuration_from_environment(credential_resolver),
            credential_context=credential_resolver,
        )
    host = os.environ.get("BUYER_OPS_CONTROL_HOST", "0.0.0.0")
    port = int(os.environ.get("BUYER_OPS_CONTROL_PORT", "8090"))
    plane = ControlPlane(
        dsn,
        permit_secret=secret.encode(),
        control_token=token,
        release_public_keys=release_public_keys,
        gate_registry_path=Path(registry_path),
        ingress_provider_runtime_factory=ingress_factory,
        ingress_webhook_factory=ingress_factory,
        journey_view_policy=journey_view_policy,
        connector_adapters=connector_adapters,
        cognitive_runtime=cognitive_runtime,
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
