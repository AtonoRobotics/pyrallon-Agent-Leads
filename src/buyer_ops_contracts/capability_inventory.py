"""Cryptographic verification for governed connector capability inventories."""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any, Protocol

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class CapabilityInventoryStore(Protocol):
    def current_inventory(self, tenant_id: str, connector_id: str) -> dict[str, Any] | None: ...


class InventorySigningKeyResolver(Protocol):
    def resolve(
        self, tenant_id: str, connector_id: str, key_id: str
    ) -> Ed25519PublicKey | None: ...


def inventory_digest_material(inventory: dict[str, Any]) -> bytes:
    material = dict(inventory)
    material.pop("signature", None)
    material.pop("inventoryDigest", None)
    return rfc8785.dumps(material)


def inventory_signature_material(inventory: dict[str, Any]) -> bytes:
    material = dict(inventory)
    material.pop("signature", None)
    return rfc8785.dumps(material)


def calculate_inventory_digest(inventory: dict[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(inventory_digest_material(inventory)).hexdigest()}"


class Ed25519CapabilityInventoryAuthority:
    """Resolve current inventory and verify its digest and Ed25519 signature."""

    def __init__(self, store: CapabilityInventoryStore, keys: InventorySigningKeyResolver) -> None:
        self._store = store
        self._keys = keys

    def current_inventory(self, tenant_id: str, connector_id: str) -> dict[str, Any] | None:
        return self._store.current_inventory(tenant_id, connector_id)

    def verify_signature(self, inventory: dict[str, Any]) -> bool:
        if inventory.get("canonicalizationVersion") != "jcs-rfc8785/1":
            return False
        expected_digest = calculate_inventory_digest(inventory)
        if not hmac.compare_digest(expected_digest, str(inventory.get("inventoryDigest", ""))):
            return False
        signature = inventory.get("signature")
        if not isinstance(signature, dict) or signature.get("algorithm") != "Ed25519":
            return False
        key = self._keys.resolve(
            str(inventory.get("tenantId", "")),
            str(inventory.get("connectorId", "")),
            str(signature.get("keyId", "")),
        )
        if key is None:
            return False
        encoded = signature.get("value")
        if not isinstance(encoded, str):
            return False
        try:
            padding = "=" * (-len(encoded) % 4)
            key.verify(
                base64.urlsafe_b64decode(encoded + padding),
                inventory_signature_material(inventory),
            )
        except (InvalidSignature, ValueError):
            return False
        return True
