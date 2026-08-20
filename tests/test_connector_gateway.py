from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from buyer_ops_contracts.connector_gateway import ConnectorGateway, ConnectorRejected
from buyer_ops_contracts.habitat import HabitatDecision
from buyer_ops_contracts.habitat_repository import (
    HabitatRegistration,
    RedeemedEffectPermit,
)


def _request(capability: str = "send") -> dict:
    return {
        "messageType": "connector_request",
        "schemaVersion": "connector-gateway/1.0.0",
        "tenantId": "tenant-1",
        "connectorId": "connector-1",
        "grantId": "grant-1",
        "grantVersion": 3,
        "capability": capability,
        "delegatedPrincipalId": "principal-1",
        "correlationId": "correlation-1",
        "occurredAt": "2026-08-19T12:00:00Z",
        "requestId": "request-1",
        "idempotencyKey": "idempotency-1",
        "payloadDigest": "sha256:f75cc554ec6bb300e5614fb8f148b161b5cc3ff53ecee7bbe35b47292c35808d",
        "expectedProviderVersion": None,
    }


def _preview() -> dict:
    return {
        "schemaVersion": "open-019-024/1.1.0",
        "tenantId": "tenant-1",
        "recordId": "preview-1",
        "recordVersion": 1,
        "observedAt": "2026-08-19T12:00:00Z",
        "effectiveFrom": "2026-08-19T12:00:00Z",
        "status": "current",
        "evidenceRefs": ["draft-evidence-1"],
        "recordType": "EffectDraftPreview",
        "connectorId": "connector-1",
        "inventoryRecordId": "inventory-1",
        "inventoryRecordVersion": 1,
        "inventoryDigest": "sha256:" + "1" * 64,
        "grantId": "grant-1",
        "grantVersion": 3,
        "delegatedPrincipalId": "principal-1",
        "capability": "send",
        "actionClass": "send_message",
        "payloadCanonicalizationVersion": "normalized-payload/1",
        "payloadDigest": "sha256:f75cc554ec6bb300e5614fb8f148b161b5cc3ff53ecee7bbe35b47292c35808d",
        "idempotencyKey": "idempotency-1",
        "targetRefs": ["conversation-1"],
        "recipientRefs": ["endpoint-1"],
        "requestedExecutionWindow": {
            "notBefore": "2026-08-19T12:00:00Z",
            "expiresAt": "2026-08-19T12:05:00Z",
        },
        "authorityClass": "effect",
        "reversible": True,
    }


def _inventory(*, capabilities: list[str] | None = None) -> dict:
    declared_capabilities = capabilities if capabilities is not None else ["read", "send"]
    has_send = "send" in declared_capabilities
    return {
        "schemaVersion": "open-019-024/1.1.0",
        "tenantId": "tenant-1",
        "recordId": "inventory-1",
        "recordVersion": 1,
        "observedAt": "2026-08-19T11:59:00Z",
        "effectiveFrom": "2026-08-19T11:59:00Z",
        "expiresAt": "2026-08-19T12:05:00Z",
        "status": "current",
        "evidenceRefs": ["connector-attestation-1"],
        "recordType": "CapabilityInventory",
        "connectorId": "connector-1",
        "connectorVersion": "3.0.0",
        "capabilities": declared_capabilities,
        "effectClasses": ["send_message"] if has_send else [],
        "capabilityEffects": [
            {
                "capability": "send",
                "actionClasses": ["send_message"],
                "constraintDigest": "sha256:" + "3" * 64,
            }
        ]
        if has_send
        else [],
        "canonicalizationVersion": "jcs-rfc8785/1",
        "inventoryDigest": "sha256:" + "1" * 64,
        "signature": {"algorithm": "Ed25519", "keyId": "connector-key-1", "value": "sig"},
    }


def _registration() -> HabitatRegistration:
    now = datetime(2026, 8, 19, 12, tzinfo=UTC)
    return HabitatRegistration(
        decision=HabitatDecision(True, "allowed"),
        permit=RedeemedEffectPermit(
            permit_digest="sha256:" + "b" * 64,
            intent_id="intent-1",
            tenant_id="tenant-1",
            principal_id="principal-1",
            action_class="send_message",
            connector_binding_id="connector-1",
            target_resource_type="Conversation",
            target_resource_id="conversation-1",
            recipient_type="endpoint",
            recipient_id="endpoint-1",
            payload_digest="sha256:f75cc554ec6bb300e5614fb8f148b161b5cc3ff53ecee7bbe35b47292c35808d",
            idempotency_key="idempotency-1",
            canonical_version_vector={"conversation-1": 4},
            issued_at=now,
            expires_at=now + timedelta(seconds=30),
            redeemed_at=now,
        ),
        attempt={"id": "attempt-1"},
    )


class _GrantAuthorizer:
    def authorize(self, request: dict) -> bool:
        return request["grantId"] == "grant-1" and request["grantVersion"] == 3


class _Adapter:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, request: dict, payload: bytes) -> dict:
        self.calls += 1
        assert payload == b"normalized-payload"
        return {
            **{
                key: request[key]
                for key in (
                    "schemaVersion",
                    "tenantId",
                    "connectorId",
                    "grantId",
                    "grantVersion",
                    "capability",
                    "delegatedPrincipalId",
                    "correlationId",
                )
            },
            "messageType": "connector_response",
            "occurredAt": "2026-08-19T12:00:01Z",
            "requestId": request["requestId"],
            "receiptId": "receipt-1",
            "outcome": "confirmed",
            "providerVersion": "v4",
            "payloadDigest": request["payloadDigest"],
        }


class _DigestMismatchAdapter(_Adapter):
    def invoke(self, request: dict, payload: bytes) -> dict:
        response = super().invoke(request, payload)
        response["payloadDigest"] = "sha256:" + "c" * 64
        return response


class _InventoryAuthority:
    def __init__(self, inventory: dict | None = None, *, signature_valid: bool = True) -> None:
        self.inventory = inventory if inventory is not None else _inventory()
        self.signature_valid = signature_valid

    def current_inventory(self, tenant_id: str, connector_id: str) -> dict | None:
        assert tenant_id == "tenant-1"
        assert connector_id == "connector-1"
        return self.inventory

    def verify_signature(self, inventory: dict) -> bool:
        return self.signature_valid and inventory is self.inventory


class _ActivationAuthority:
    def __init__(self, active: bool = True) -> None:
        self.active = active

    def authorizes(self, request: dict, *, evaluated_at: datetime) -> bool:
        return self.active


def _gateway(
    adapter: _Adapter, inventory_authority: _InventoryAuthority | None = None
) -> ConnectorGateway:
    return ConnectorGateway(
        _GrantAuthorizer(),
        adapter,
        inventory_authority or _InventoryAuthority(),
        _ActivationAuthority(),
        clock=lambda: datetime(2026, 8, 19, 12, tzinfo=UTC),
    )


def test_provider_changing_connector_call_requires_matching_redeemed_permit() -> None:
    adapter = _Adapter()
    gateway = _gateway(adapter)
    response = gateway.invoke(
        _request(), b"normalized-payload", registration=_registration(), preview=_preview()
    )
    assert response["outcome"] == "confirmed"
    assert adapter.calls == 1

    mismatched = replace(
        _registration(),
        permit=replace(_registration().permit, payload_digest="sha256:" + "d" * 64),
    )
    with pytest.raises(ConnectorRejected) as raised:
        gateway.invoke(
            _request(), b"normalized-payload", registration=mismatched, preview=_preview()
        )
    assert raised.value.code == "permit_mismatch"
    assert adapter.calls == 1


def test_connector_response_must_bind_the_exact_request_payload_digest() -> None:
    adapter = _DigestMismatchAdapter()

    with pytest.raises(ConnectorRejected) as raised:
        _gateway(adapter).invoke(
            _request(), b"normalized-payload", registration=_registration(), preview=_preview()
        )

    assert raised.value.code == "connector_response_mismatch"
    assert adapter.calls == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("targetRefs", ["conversation-other"]),
        ("recipientRefs", ["endpoint-other"]),
    ],
)
def test_effect_permit_target_and_recipient_must_be_present_in_preview(
    field: str, value: list[str]
) -> None:
    adapter = _Adapter()
    preview = _preview()
    preview[field] = value

    with pytest.raises(ConnectorRejected) as raised:
        _gateway(adapter).invoke(
            _request(), b"normalized-payload", registration=_registration(), preview=preview
        )

    assert raised.value.code == "permit_mismatch"
    assert adapter.calls == 0


def test_provider_changing_connector_call_rejects_permit_at_expiry_boundary() -> None:
    adapter = _Adapter()
    gateway = _gateway(adapter)
    now = datetime(2026, 8, 19, 12, tzinfo=UTC)
    expired = replace(
        _registration(),
        permit=replace(_registration().permit, expires_at=now),
    )

    with pytest.raises(ConnectorRejected) as raised:
        gateway.invoke(_request(), b"normalized-payload", registration=expired, preview=_preview())

    assert raised.value.code == "permit_mismatch"
    assert adapter.calls == 0


def test_provider_changing_call_requires_effect_draft_preview() -> None:
    adapter = _Adapter()
    gateway = _gateway(adapter)
    with pytest.raises(ConnectorRejected) as raised:
        gateway.invoke(_request(), b"normalized-payload", registration=_registration())
    assert raised.value.code == "effect_draft_preview_required"
    assert adapter.calls == 0


def test_read_connector_call_has_no_effect_permit_but_still_requires_current_grant() -> None:
    adapter = _Adapter()
    gateway = _gateway(adapter)
    assert (
        gateway.invoke(_request("read"), b"normalized-payload", registration=None)["outcome"]
        == "confirmed"
    )


def test_signed_inventory_not_capability_name_determines_effect_bearing_call() -> None:
    inventory = _inventory(capabilities=["read", "reconcile"])
    inventory["effectClasses"] = ["reconcile_provider_state"]
    inventory["capabilityEffects"] = [
        {
            "capability": "reconcile",
            "actionClasses": ["reconcile_provider_state"],
            "constraintDigest": "sha256:" + "4" * 64,
        }
    ]
    preview = {
        **_preview(),
        "capability": "reconcile",
        "actionClass": "reconcile_provider_state",
    }
    adapter = _Adapter()
    gateway = _gateway(adapter, _InventoryAuthority(inventory))

    with pytest.raises(ConnectorRejected) as raised:
        gateway.invoke(
            _request("reconcile"),
            b"normalized-payload",
            registration=None,
            preview=preview,
        )

    assert raised.value.code == "effect_permit_required"
    assert adapter.calls == 0


def test_connector_rejects_revoked_grant_before_adapter_invocation() -> None:
    class Revoked:
        def authorize(self, request: dict) -> bool:
            return False

    adapter = _Adapter()
    with pytest.raises(ConnectorRejected) as raised:
        ConnectorGateway(
            Revoked(),
            adapter,
            _InventoryAuthority(),
            _ActivationAuthority(),
            clock=lambda: datetime(2026, 8, 19, 12, tzinfo=UTC),
        ).invoke(_request(), b"normalized-payload", registration=_registration())
    assert raised.value.code == "connector_grant_revoked"
    assert adapter.calls == 0


@pytest.mark.parametrize(
    ("authority", "code"),
    [
        (_InventoryAuthority(None), "capability_inventory_required"),
        (_InventoryAuthority(signature_valid=False), "capability_inventory_signature_invalid"),
        (_InventoryAuthority(_inventory(capabilities=["read"])), "capability_not_in_inventory"),
    ],
)
def test_connector_fails_closed_without_current_signed_capability(
    authority: _InventoryAuthority, code: str
) -> None:
    if code == "capability_inventory_required":
        authority.inventory = None
    adapter = _Adapter()
    gateway = ConnectorGateway(
        _GrantAuthorizer(),
        adapter,
        authority,
        _ActivationAuthority(),
        clock=lambda: datetime(2026, 8, 19, 12, tzinfo=UTC),
    )
    with pytest.raises(ConnectorRejected) as raised:
        gateway.invoke(
            _request(), b"normalized-payload", registration=_registration(), preview=_preview()
        )
    assert raised.value.code == code
    assert adapter.calls == 0


def test_provider_changing_call_requires_release_capability_activation() -> None:
    adapter = _Adapter()
    gateway = ConnectorGateway(
        _GrantAuthorizer(),
        adapter,
        _InventoryAuthority(),
        _ActivationAuthority(False),
        clock=lambda: datetime(2026, 8, 19, 12, tzinfo=UTC),
    )
    with pytest.raises(ConnectorRejected) as raised:
        gateway.invoke(
            _request(), b"normalized-payload", registration=_registration(), preview=_preview()
        )
    assert raised.value.code == "release_capability_not_activated"
    assert adapter.calls == 0


def test_preview_binds_capability_action_class_and_execution_window() -> None:
    adapter = _Adapter()
    gateway = _gateway(adapter)
    cases = (
        ({**_preview(), "capability": "update"}, "preview_capability_mismatch"),
        ({**_preview(), "actionClass": "update_record"}, "effect_class_not_in_inventory"),
        (
            {
                **_preview(),
                "requestedExecutionWindow": {
                    "notBefore": "2026-08-19T12:01:00Z",
                    "expiresAt": "2026-08-19T12:05:00Z",
                },
            },
            "preview_not_yet_effective",
        ),
        (
            {
                **_preview(),
                "requestedExecutionWindow": {
                    "notBefore": "2026-08-19T11:00:00Z",
                    "expiresAt": "2026-08-19T12:00:00Z",
                },
            },
            "preview_expired",
        ),
    )
    for preview, code in cases:
        with pytest.raises(ConnectorRejected) as raised:
            gateway.invoke(
                _request(), b"normalized-payload", registration=_registration(), preview=preview
            )
        assert raised.value.code == code
    assert adapter.calls == 0
