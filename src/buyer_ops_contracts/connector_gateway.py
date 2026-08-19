"""Provider-neutral connector boundary with mandatory current grant and effect permit."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from .closure import validate_closure_semantics
from .habitat_repository import HabitatRegistration, RedeemedEffectPermit
from .structural import validate_record

_EFFECT_CAPABILITIES = frozenset({"create", "update", "send", "schedule"})
_BOUND_BASE_FIELDS = (
    "schemaVersion",
    "tenantId",
    "connectorId",
    "grantId",
    "grantVersion",
    "capability",
    "delegatedPrincipalId",
    "correlationId",
)


class ConnectorRejected(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ConnectorGrantAuthorizer(Protocol):
    """Re-read current connector grant, delegated principal, scopes, and revocation."""

    def authorize(self, request: dict[str, Any]) -> bool: ...


class ConnectorAdapter(Protocol):
    """Provider adapter owns credentials; the gateway never receives them."""

    def invoke(self, request: dict[str, Any], payload: bytes) -> dict[str, Any]: ...


class CapabilityInventoryAuthority(Protocol):
    """Resolve the one current inventory and verify its governed signature."""

    def current_inventory(self, tenant_id: str, connector_id: str) -> dict[str, Any] | None: ...

    def verify_signature(self, inventory: dict[str, Any]) -> bool: ...


class ReleaseActivationAuthority(Protocol):
    """Resolve capability mapping from the governed activation contract."""

    def authorizes(self, request: dict[str, Any], *, evaluated_at: datetime) -> bool: ...


class ConnectorGateway:
    def __init__(
        self,
        grants: ConnectorGrantAuthorizer,
        adapter: ConnectorAdapter,
        inventories: CapabilityInventoryAuthority,
        activation: ReleaseActivationAuthority,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._grants = grants
        self._adapter = adapter
        self._inventories = inventories
        self._activation = activation
        self._clock = clock

    def invoke(
        self,
        request: dict[str, Any],
        payload: bytes,
        *,
        registration: HabitatRegistration | None,
        preview: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        validate_record(request, "connector_gateway")
        if request.get("messageType") != "connector_request":
            raise ConnectorRejected("connector_request_invalid")
        self._verify_payload(request, payload)
        if not self._grants.authorize(request):
            raise ConnectorRejected("connector_grant_revoked")
        now = self._clock().astimezone(UTC)
        inventory = self._verify_inventory(request, now)
        if request["capability"] in _EFFECT_CAPABILITIES:
            if not self._activation.authorizes(request, evaluated_at=now):
                raise ConnectorRejected("release_capability_not_activated")
            self._verify_draft_preview(request, preview, inventory, now)
            self._verify_effect_permit(request, registration, preview, now)
        response = self._adapter.invoke(request, payload)
        validate_record(response, "connector_gateway")
        if (
            response.get("messageType") != "connector_response"
            or any(response.get(field) != request[field] for field in _BOUND_BASE_FIELDS)
            or response.get("requestId") != request["requestId"]
        ):
            raise ConnectorRejected("connector_response_mismatch")
        return response

    @staticmethod
    def _verify_payload(request: dict[str, Any], payload: bytes) -> None:
        expected = request["payloadDigest"]
        algorithm, separator, digest = expected.partition(":")
        if separator != ":" or algorithm != "sha256":
            raise ConnectorRejected("payload_digest_algorithm_unsupported")
        actual = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(actual.lower(), digest.lower()):
            raise ConnectorRejected("payload_digest_mismatch")

    def _verify_inventory(self, request: dict[str, Any], now: datetime) -> dict[str, Any]:
        inventory = self._inventories.current_inventory(request["tenantId"], request["connectorId"])
        if inventory is None:
            raise ConnectorRejected("capability_inventory_required")
        validate_record(inventory, "closure")
        validate_closure_semantics(inventory, now=now)
        if (
            inventory.get("recordType") != "CapabilityInventory"
            or inventory.get("tenantId") != request["tenantId"]
            or inventory.get("connectorId") != request["connectorId"]
            or inventory.get("status") != "current"
        ):
            raise ConnectorRejected("capability_inventory_mismatch")
        if not self._inventories.verify_signature(inventory):
            raise ConnectorRejected("capability_inventory_signature_invalid")
        if _timestamp(inventory["effectiveFrom"]) > now:
            raise ConnectorRejected("capability_inventory_not_yet_effective")
        if _timestamp(inventory["expiresAt"]) <= now:
            raise ConnectorRejected("capability_inventory_expired")
        if request["capability"] not in inventory["capabilities"]:
            raise ConnectorRejected("capability_not_in_inventory")
        return inventory

    @staticmethod
    def _verify_draft_preview(
        request: dict[str, Any],
        preview: dict[str, Any] | None,
        inventory: dict[str, Any],
        now: datetime,
    ) -> None:
        if preview is None:
            raise ConnectorRejected("effect_draft_preview_required")
        validate_record(preview, "closure")
        validate_closure_semantics(preview)
        if (
            preview.get("recordType") != "EffectDraftPreview"
            or preview.get("tenantId") != request["tenantId"]
            or preview.get("connectorId") != request["connectorId"]
            or preview.get("payloadDigest") != request["payloadDigest"]
        ):
            raise ConnectorRejected("preview_digest_mismatch")
        if preview["capability"] != request["capability"]:
            raise ConnectorRejected("preview_capability_mismatch")
        bound_fields = {
            "inventoryRecordId": inventory["recordId"],
            "inventoryRecordVersion": inventory["recordVersion"],
            "inventoryDigest": inventory["inventoryDigest"],
            "grantId": request["grantId"],
            "grantVersion": request["grantVersion"],
            "delegatedPrincipalId": request["delegatedPrincipalId"],
            "idempotencyKey": request["idempotencyKey"],
        }
        if any(preview.get(field) != value for field, value in bound_fields.items()):
            raise ConnectorRejected("preview_binding_mismatch")
        action_class = preview["actionClass"]
        mappings = [
            mapping
            for mapping in inventory["capabilityEffects"]
            if mapping["capability"] == request["capability"]
            and action_class in mapping["actionClasses"]
        ]
        if action_class not in inventory["effectClasses"] or len(mappings) != 1:
            raise ConnectorRejected("effect_class_not_in_inventory")
        window = preview["requestedExecutionWindow"]
        if _timestamp(window["notBefore"]) > now:
            raise ConnectorRejected("preview_not_yet_effective")
        if _timestamp(window["expiresAt"]) <= now:
            raise ConnectorRejected("preview_expired")

    @staticmethod
    def _verify_effect_permit(
        request: dict[str, Any],
        registration: HabitatRegistration | None,
        preview: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        if (
            registration is None
            or not registration.decision.allowed
            or registration.permit is None
            or registration.attempt is None
        ):
            raise ConnectorRejected("effect_permit_required")
        permit: RedeemedEffectPermit = registration.permit
        if permit.state != "redeemed" or any(
            (
                permit.tenant_id != request["tenantId"],
                permit.principal_id != request["delegatedPrincipalId"],
                permit.connector_binding_id != request["connectorId"],
                permit.payload_digest != request["payloadDigest"],
                preview is not None and permit.payload_digest != preview.get("payloadDigest"),
                preview is not None and permit.action_class != preview.get("actionClass"),
                permit.idempotency_key != request["idempotencyKey"],
                now is not None and permit.issued_at > now,
                now is not None and permit.expires_at < now,
                now is not None and permit.redeemed_at > now,
            )
        ):
            raise ConnectorRejected("permit_mismatch")


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ConnectorRejected("timestamp_offset_required")
    return parsed.astimezone(UTC)
