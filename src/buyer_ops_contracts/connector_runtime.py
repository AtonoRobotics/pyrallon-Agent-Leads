"""Production connector runtime wiring for the governed effect gateway.

The runtime owns provider transport and credentials.  The gateway owns every
tenant, grant, inventory, activation, permit, payload, and response binding.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .activation import ActivationController
from .canonical_repository import CanonicalRepository
from .capability_inventory import Ed25519CapabilityInventoryAuthority
from .closure_repository import PostgresClosureRepository
from .connector_authorization import (
    PlatformOAuthStore,
    load_connector_credential,
    refresh_connector_credential,
)
from .connector_gateway import (
    ConnectorAdapter,
    ConnectorGateway,
    ConnectorGrantAuthorizer,
    ConnectorRejected,
    ReleaseActivationAuthority,
)
from .habitat import HabitatDecision
from .habitat_repository import HabitatRegistration, RedeemedEffectPermit
from .provider_adapters import (
    DirectProviderAdapter,
    DirectProviderConfig,
    ProviderAdapterError,
    configured_direct_provider_adapters,
)
from .structural import validate_record


class ConnectorRuntimeError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class ConnectorAdapterConfig:
    connector_id: str
    endpoint: str
    secret_env: str
    provider_version: str | None
    timeout_seconds: float

    @classmethod
    def from_value(cls, value: Any) -> ConnectorAdapterConfig:
        if not isinstance(value, dict):
            raise ValueError("connector adapter configuration must be an object")
        connector_id = _required(value, "connectorId")
        endpoint = _required(value, "endpoint")
        if not endpoint.startswith("https://"):
            allow_local_http = os.environ.get("BUYER_OPS_ALLOW_INSECURE_CONNECTOR_HTTP") == "true"
            if not (
                allow_local_http
                and endpoint.startswith("http://")
                and endpoint.split("/", 3)[2].split(":", 1)[0] in {"127.0.0.1", "localhost", "::1"}
            ):
                raise ValueError("connector adapter endpoints must use HTTPS")
        secret_env = _required(value, "secretEnv")
        timeout = float(value.get("timeoutSeconds", 15))
        if not 0 < timeout <= 60:
            raise ValueError("connector adapter timeoutSeconds must be between 0 and 60")
        return cls(
            connector_id=connector_id,
            endpoint=endpoint,
            secret_env=secret_env,
            provider_version=str(value["providerVersion"])
            if value.get("providerVersion")
            else None,
            timeout_seconds=timeout,
        )


class HttpsConnectorAdapter(ConnectorAdapter):
    """Call a product-owned HTTPS/MCP adapter without exposing its credential."""

    def __init__(self, config: ConnectorAdapterConfig) -> None:
        secret = os.environ.get(config.secret_env, "")
        if len(secret) < 16:
            raise ValueError(f"connector secret env {config.secret_env} is missing or too short")
        self._config = config
        self._secret = secret

    def invoke(self, request: dict[str, Any], payload: bytes) -> dict[str, Any]:
        body = json.dumps(
            {
                "request": request,
                "payloadBase64": base64.b64encode(payload).decode("ascii"),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        http_request = urllib.request.Request(
            self._config.endpoint,
            data=body,
            method="POST",
            headers={
                "accept": "application/json",
                "authorization": f"Bearer {self._secret}",
                "content-type": "application/json",
                "x-buyer-ops-connector": self._config.connector_id,
                "x-buyer-ops-request-id": str(request["requestId"]),
                "x-buyer-ops-idempotency-key": str(request["idempotencyKey"]),
            },
        )
        try:
            with urllib.request.urlopen(
                http_request, timeout=self._config.timeout_seconds
            ) as response:
                status = int(response.status)
                headers = response.headers
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            headers = exc.headers
            raw = exc.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ConnectorRuntimeError(
                "connector_unavailable", "connector adapter is unreachable"
            ) from exc
        try:
            provider = json.loads(raw.decode()) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectorRuntimeError(
                "provider_response_invalid", "connector adapter returned invalid JSON"
            ) from exc
        if not isinstance(provider, dict):
            raise ConnectorRuntimeError(
                "provider_response_invalid", "connector adapter response must be an object"
            )
        outcome = _outcome(status, provider)
        receipt = str(provider.get("receiptId") or headers.get("x-provider-receipt") or "")
        if not receipt:
            raise ConnectorRuntimeError(
                "provider_response_invalid", "connector adapter omitted provider receipt"
            )
        provider_version = (
            str(provider.get("providerVersion") or headers.get("x-provider-version") or "")
            or self._config.provider_version
        )
        response = {
            "messageType": "connector_response",
            "schemaVersion": "connector-gateway/1.0.0",
            "tenantId": request["tenantId"],
            "connectorId": request["connectorId"],
            "grantId": request["grantId"],
            "grantVersion": request["grantVersion"],
            "capability": request["capability"],
            "delegatedPrincipalId": request["delegatedPrincipalId"],
            "correlationId": request["correlationId"],
            "occurredAt": request["occurredAt"],
            "requestId": request["requestId"],
            "receiptId": receipt,
            "outcome": outcome,
            "providerVersion": provider_version,
            "payloadDigest": request["payloadDigest"],
        }
        validate_record(response, "connector_gateway")
        return response

    def reconcile(self, request: dict[str, Any], provider_receipt_id: str | None) -> dict[str, Any]:
        """Ask the adapter for provider truth without replaying the effect."""
        body = json.dumps(
            {"request": request, "providerReceiptId": provider_receipt_id},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        http_request = urllib.request.Request(
            self._config.endpoint.rstrip("/") + "/reconcile",
            data=body,
            method="POST",
            headers={
                "accept": "application/json",
                "authorization": f"Bearer {self._secret}",
                "content-type": "application/json",
                "x-buyer-ops-connector": self._config.connector_id,
            },
        )
        try:
            with urllib.request.urlopen(
                http_request, timeout=self._config.timeout_seconds
            ) as http_response:
                raw = http_response.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise ConnectorRuntimeError(
                "connector_unavailable", "connector reconciliation is unreachable"
            ) from exc
        try:
            decoded = json.loads(raw.decode()) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectorRuntimeError(
                "provider_response_invalid", "connector reconciliation returned invalid JSON"
            ) from exc
        if not isinstance(decoded, dict):
            raise ConnectorRuntimeError(
                "provider_response_invalid", "connector reconciliation must return an object"
            )
        response: dict[str, Any] = decoded
        state = response.get("attemptState")
        if state not in {"unknown_outcome", "reconciled_failed", "reconciled_succeeded"}:
            raise ConnectorRuntimeError(
                "provider_response_invalid", "connector reconciliation returned an invalid state"
            )
        return response


class _GrantAuthority(ConnectorGrantAuthorizer):
    def __init__(self, repository: CanonicalRepository, *, clock: Callable[[], datetime]) -> None:
        self._repository = repository
        self._clock = clock

    def authorize(self, request: dict[str, Any]) -> bool:
        grant = self._repository.get(str(request["grantId"]))
        if grant is None or grant.get("recordType") != "ConnectorGrant":
            return False
        if grant.get("tenantId") != request["tenantId"] or grant.get("grantState") != "active":
            return False
        if int(grant.get("version", 0)) != int(request["grantVersion"]):
            return False
        if grant.get("connectorId") != request["connectorId"]:
            return False
        if grant.get("delegatedPrincipalId") != request["delegatedPrincipalId"]:
            return False
        if request["capability"] not in grant.get("capabilities", []):
            return False
        expiry = grant.get("expiresAt")
        return not (expiry is not None and _timestamp(str(expiry)) <= self._clock().astimezone(UTC))


class _InventoryKeyResolver:
    def __init__(self, keys: dict[str, Ed25519PublicKey]) -> None:
        self._keys = keys

    def resolve(self, tenant_id: str, connector_id: str, key_id: str) -> Ed25519PublicKey | None:
        del tenant_id, connector_id
        return self._keys.get(key_id)


class _ActivationAuthority(ReleaseActivationAuthority):
    def __init__(self, controller: ActivationController) -> None:
        self._controller = controller

    def authorizes(self, request: dict[str, Any], *, evaluated_at: datetime) -> bool:
        del evaluated_at
        return self._controller.capability_activated(str(request["capability"]))


class _PermitReader:
    def __init__(self, connection: Any, *, tenant_id: str) -> None:
        self._connection = connection
        self._tenant_id = tenant_id

    def read(self, permit_digest: str) -> HabitatRegistration | None:
        if not permit_digest:
            return None
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
            cursor.execute(
                """
                SELECT permit_digest, intent_id, tenant_id, principal_id, action_class,
                       connector_binding_id, target_resource_type, target_resource_id,
                       recipient_type, recipient_id, payload_digest, idempotency_key,
                       canonical_version_vector, issued_at, expires_at, redeemed_at, state,
                       dispatch_claimed_at
                FROM habitat_effect_permits
                WHERE tenant_id = %s AND permit_digest = %s
                FOR UPDATE
                """.strip(),
                (self._tenant_id, permit_digest),
            )
            row = cursor.fetchone()
            if row is None:
                self._connection.commit()
                return None
            if row[17] is not None:
                self._connection.commit()
                return HabitatRegistration(HabitatDecision(False, "permit_replayed"))
            cursor.execute(
                """
                UPDATE habitat_effect_permits
                SET dispatch_claimed_at = clock_timestamp()
                WHERE tenant_id = %s AND permit_digest = %s AND dispatch_claimed_at IS NULL
                """.strip(),
                (self._tenant_id, permit_digest),
            )
            permit = RedeemedEffectPermit(
                permit_digest=str(row[0]),
                intent_id=str(row[1]),
                tenant_id=str(row[2]),
                principal_id=str(row[3]),
                action_class=str(row[4]),
                connector_binding_id=str(row[5]),
                target_resource_type=str(row[6]),
                target_resource_id=str(row[7]),
                recipient_type=str(row[8]),
                recipient_id=str(row[9]),
                payload_digest=str(row[10]),
                idempotency_key=str(row[11]),
                canonical_version_vector=dict(row[12]),
                issued_at=row[13],
                expires_at=row[14],
                redeemed_at=row[15],
                state=str(row[16]),
            )
            cursor.execute(
                "SELECT record FROM canonical_records_current WHERE tenant_id = %s AND record_id = %s",
                (self._tenant_id, f"effect-attempt:{permit.intent_id}"),
            )
            attempt_row = cursor.fetchone()
        self._connection.commit()
        attempt = attempt_row[0] if attempt_row and isinstance(attempt_row[0], dict) else None
        decision = HabitatDecision(attempt is not None and permit.state == "redeemed", "admitted")
        return HabitatRegistration(decision, permit, attempt)


class PostgresConnectorRuntime:
    def __init__(
        self,
        connection: Any,
        *,
        tenant_id: str,
        activation: ActivationController,
        adapters: dict[str, ConnectorAdapter],
        permit_secret: bytes | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._connection = connection
        self._tenant_id = tenant_id
        self._activation = activation
        self._adapters = adapters
        self._permit_secret = permit_secret
        self._clock = clock

    def invoke(
        self,
        request: dict[str, Any],
        payload: bytes,
        *,
        permit_digest: str,
        preview: dict[str, Any] | None,
    ) -> dict[str, Any]:
        adapter = self._adapter_for_request(request)
        if adapter is None:
            raise ConnectorRejected("connector_unavailable")
        repository = CanonicalRepository(self._connection, tenant_id=self._tenant_id)
        inventory_store = PostgresClosureRepository(self._connection, tenant_id=self._tenant_id)
        keys = _inventory_keys_from_environment()
        inventory = Ed25519CapabilityInventoryAuthority(
            inventory_store, _InventoryKeyResolver(keys)
        )
        gateway = ConnectorGateway(
            _GrantAuthority(repository, clock=self._clock),
            adapter,
            inventory,
            _ActivationAuthority(self._activation),
            clock=self._clock,
        )
        registration = _PermitReader(self._connection, tenant_id=self._tenant_id).read(
            permit_digest
        )
        attempt = None if registration is None else registration.attempt
        if registration is not None and attempt is not None:
            attempt = _save_effect_attempt(
                repository,
                attempt,
                attempt_state="dispatching",
            )
            registration = HabitatRegistration(registration.decision, registration.permit, attempt)
        try:
            response = gateway.invoke(request, payload, registration=registration, preview=preview)
        except ProviderAdapterError as exc:
            if attempt is not None:
                _save_effect_attempt(
                    repository,
                    attempt,
                    attempt_state="unknown_outcome" if exc.retryable else "rejected",
                )
            raise
        except ConnectorRuntimeError:
            if attempt is not None:
                _save_effect_attempt(repository, attempt, attempt_state="unknown_outcome")
            raise
        except ConnectorRejected as exc:
            if attempt is not None:
                _save_effect_attempt(
                    repository,
                    attempt,
                    attempt_state=(
                        "unknown_outcome"
                        if exc.code == "connector_response_mismatch"
                        else "rejected"
                    ),
                )
            raise
        if attempt is not None:
            outcome = str(response.get("outcome"))
            state = {
                "confirmed": "confirmed",
                "rejected": "rejected",
                "conflict": "unknown_outcome",
                "unknown": "unknown_outcome",
                "revoked": "rejected",
            }.get(outcome)
            if state is None:
                raise ConnectorRuntimeError(
                    "provider_response_invalid", "provider response outcome is invalid"
                )
            _save_effect_attempt(
                repository,
                attempt,
                attempt_state=state,
                provider_receipt_id=str(response.get("receiptId"))
                if response.get("receiptId")
                else None,
            )
        return response

    def reconcile(self, request: dict[str, Any], provider_receipt_id: str | None) -> dict[str, Any]:
        adapter = self._adapter_for_request(request)
        if adapter is None:
            raise ConnectorRuntimeError("connector_unavailable", "connector adapter is unavailable")
        reconcile = getattr(adapter, "reconcile", None)
        if not callable(reconcile):
            raise ConnectorRuntimeError(
                "provider_response_invalid", "connector adapter does not support reconciliation"
            )
        try:
            response = reconcile(request, provider_receipt_id or "")
        except ProviderAdapterError as exc:
            raise ConnectorRuntimeError(exc.code, exc.detail) from exc
        if not isinstance(response, dict):
            raise ConnectorRuntimeError(
                "provider_response_invalid", "connector reconciliation must return an object"
            )
        return response

    def _adapter_for_request(self, request: dict[str, Any]) -> ConnectorAdapter | None:
        connector_id = str(request.get("connectorId") or "")
        adapter = self._adapters.get(connector_id)
        if adapter is not None or self._permit_secret is None:
            return adapter
        credential = load_connector_credential(
            self._connection,
            tenant_id=self._tenant_id,
            grant_id=str(request.get("grantId") or ""),
            connector_id=connector_id,
            permit_secret=self._permit_secret,
            now=self._clock(),
        )
        if credential is None:
            credential = refresh_connector_credential(
                self._connection,
                tenant_id=self._tenant_id,
                grant_id=str(request.get("grantId") or ""),
                connector_id=connector_id,
                permit_secret=self._permit_secret,
                now=self._clock(),
                oauth_clients=PlatformOAuthStore(
                    self._connection, permit_secret=self._permit_secret
                ).material(),
            )
        if credential is None:
            return None
        stored_connector, provider, account_id, token = credential
        if stored_connector != connector_id:
            return None
        adapter_provider = {
            "google": "google_calendar",
            "microsoft": "microsoft_graph",
            "docusign": "docusign",
            "twilio": "twilio",
            "sendgrid": "sendgrid",
        }.get(provider, provider)
        config = DirectProviderConfig.from_value(
            {
                "connectorId": connector_id,
                "provider": adapter_provider,
                "credentialEnv": "BUYER_OPS_DATABASE_CREDENTIAL",
                "accountId": account_id,
            }
        )
        return DirectProviderAdapter(config, credential=token)


def _save_effect_attempt(
    repository: CanonicalRepository,
    attempt: dict[str, Any],
    *,
    attempt_state: str,
    provider_receipt_id: str | None = None,
) -> dict[str, Any]:
    """Append one governed EffectAttempt state transition after provider dispatch."""
    updated = dict(attempt)
    updated["version"] = int(attempt["version"]) + 1
    updated["updatedAt"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    updated["attemptState"] = attempt_state
    if provider_receipt_id is not None:
        updated["providerReceiptId"] = provider_receipt_id
    return repository.save(updated, expected_version=int(attempt["version"]))


def configured_adapters_from_environment() -> dict[str, ConnectorAdapter]:
    encoded = os.environ.get("BUYER_OPS_CONNECTOR_ADAPTERS_JSON", "").strip()
    if not encoded:
        raise ValueError("BUYER_OPS_CONNECTOR_ADAPTERS_JSON is required")
    try:
        values = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise ValueError("BUYER_OPS_CONNECTOR_ADAPTERS_JSON must be valid JSON") from exc
    if not isinstance(values, list) or not values:
        raise ValueError("BUYER_OPS_CONNECTOR_ADAPTERS_JSON must be a non-empty list")
    result: dict[str, ConnectorAdapter] = {}
    for value in values:
        config = ConnectorAdapterConfig.from_value(value)
        if config.connector_id in result:
            raise ValueError(f"duplicate connector adapter: {config.connector_id}")
        result[config.connector_id] = HttpsConnectorAdapter(config)
    direct_encoded = os.environ.get("BUYER_OPS_DIRECT_PROVIDER_ADAPTERS_JSON", "").strip()
    if direct_encoded:
        for connector_id, adapter in configured_direct_provider_adapters(direct_encoded).items():
            if connector_id in result:
                raise ValueError(f"duplicate connector adapter: {connector_id}")
            result[connector_id] = adapter
    return result


def _inventory_keys_from_environment() -> dict[str, Ed25519PublicKey]:
    encoded = os.environ.get("BUYER_OPS_CONNECTOR_INVENTORY_PUBLIC_KEYS_JSON", "").strip()
    if not encoded:
        return {}
    try:
        values = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise ValueError("connector inventory public keys must be valid JSON") from exc
    if not isinstance(values, dict):
        raise ValueError("connector inventory public keys must be an object")
    result: dict[str, Ed25519PublicKey] = {}
    for key_id, encoded_key in values.items():
        if not isinstance(key_id, str) or not isinstance(encoded_key, str):
            raise ValueError("connector inventory key entries must be strings")
        try:
            padding = "=" * (-len(encoded_key) % 4)
            raw = base64.urlsafe_b64decode(encoded_key + padding)
            result[key_id] = Ed25519PublicKey.from_public_bytes(raw)
        except ValueError as exc:
            raise ValueError(f"invalid connector inventory key: {key_id}") from exc
    return result


def _required(value: dict[str, Any], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result.strip():
        raise ValueError(f"connector adapter {field} is required")
    return result.strip()


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ConnectorRejected("connector_grant_expiry_invalid")
    return parsed.astimezone(UTC)


def _outcome(status: int, provider: dict[str, Any]) -> str:
    supplied = provider.get("outcome")
    if supplied in {"confirmed", "rejected", "conflict", "unknown", "revoked"}:
        return str(supplied)
    if 200 <= status < 300:
        return "confirmed"
    if status in {401, 403}:
        return "revoked"
    if status == 409:
        return "conflict"
    if 400 <= status < 500:
        return "rejected"
    return "unknown"
