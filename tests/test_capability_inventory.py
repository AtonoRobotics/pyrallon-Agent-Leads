from __future__ import annotations

import base64
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from buyer_ops_contracts.capability_inventory import (
    Ed25519CapabilityInventoryAuthority,
    calculate_inventory_digest,
    inventory_signature_material,
)


def _signed_inventory() -> tuple[dict[str, Any], Ed25519PrivateKey]:
    private_key = Ed25519PrivateKey.generate()
    inventory: dict[str, Any] = {
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
        "capabilities": ["read", "send"],
        "effectClasses": ["send_message"],
        "capabilityEffects": [
            {
                "capability": "send",
                "actionClasses": ["send_message"],
                "constraintDigest": "sha256:" + "3" * 64,
            }
        ],
        "canonicalizationVersion": "jcs-rfc8785/1",
        "inventoryDigest": "",
        "signature": {"algorithm": "Ed25519", "keyId": "key-1", "value": ""},
    }
    inventory["inventoryDigest"] = calculate_inventory_digest(inventory)
    signature = private_key.sign(inventory_signature_material(inventory))
    inventory["signature"]["value"] = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return inventory, private_key


def test_inventory_authority_verifies_exact_digest_and_signature_material() -> None:
    inventory, private_key = _signed_inventory()

    class Store:
        def current_inventory(self, tenant_id: str, connector_id: str) -> dict[str, Any] | None:
            return inventory

    class Keys:
        def resolve(self, tenant_id: str, connector_id: str, key_id: str):
            if (tenant_id, connector_id, key_id) == ("tenant-1", "connector-1", "key-1"):
                return private_key.public_key()
            return None

    authority = Ed25519CapabilityInventoryAuthority(Store(), Keys())
    assert authority.verify_signature(inventory)

    inventory["capabilities"].append("delete")
    assert authority.verify_signature(inventory) is False
