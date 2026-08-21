from __future__ import annotations

import base64
import copy
from typing import Any

import pytest
import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from buyer_ops_contracts.activation import (
    ActivationController,
    Ed25519ActivationDecisionSignatureVerifier,
)
from buyer_ops_contracts.release_evidence import ReleaseEvidenceEvaluator


def _decision() -> dict[str, Any]:
    return {
        "messageType": "activation_decision",
        "schemaVersion": "release-activation/1.1.0",
        "decisionId": "decision-1",
        "capabilityId": "all_external_effects",
        "tenantId": "tenant-1",
        "environment": "staging",
        "releaseDigest": "sha256:" + "a" * 64,
        "gateRegistryVersion": "1.0.0",
        "gateRegistryDigest": "sha256:" + "b" * 64,
        "directlyApplicableGateIds": ["GATE-002"],
        "requiredGateIds": ["GATE-002"],
        "deployedBuildDigests": {"web": "sha256:" + "c" * 64},
        "expectedActivationVersion": 0,
        "decision": "activate",
        "evidenceIds": ["release-gate-2"],
        "accessibilityEvidenceIds": ["a11y-web"],
        "accessibilityBindingIds": ["binding-web"],
        "accessibilityAcceptanceDigests": {"web": "sha256:" + "e" * 64},
        "evidenceSetDigest": "sha256:" + "d" * 64,
        "authorizedBy": "release-manager",
        "authorizationId": "authorization-1",
        "authorizationVersion": 1,
        "authorizationPolicyVersion": "policy-1",
        "authorizationRecordScopes": ["all_external_effects"],
        "decidedAt": "2030-01-01T00:00:00Z",
        "rollbackState": "armed",
        "readbackRequired": True,
    }


def test_activation_signature_verifier_binds_exact_decision_material() -> None:
    private_key = Ed25519PrivateKey.generate()
    decision = _decision()
    signature = private_key.sign(rfc8785.dumps(decision))
    decision["signature"] = {
        "keyId": "release-key-1",
        "algorithm": "ed25519",
        "value": base64.urlsafe_b64encode(signature).decode().rstrip("="),
    }
    verifier = Ed25519ActivationDecisionSignatureVerifier(
        {"release-key-1": private_key.public_key()}
    )
    assert verifier.verify(decision)
    tampered = copy.deepcopy(decision)
    tampered["releaseDigest"] = "sha256:" + "e" * 64
    assert verifier.verify(tampered) is False


class _Cursor:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self.rows = rows or []
        self.one: tuple[object, ...] | None = rows[0] if rows else None

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        del statement, parameters

    def fetchone(self) -> tuple[object, ...] | None:
        return self.one

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class _Connection:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self.cursor_instance = _Cursor(rows)

    def cursor(self) -> _Cursor:
        return self.cursor_instance

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


class _Disablement:
    def proves_disabled(self, capability_id: str, evidence_refs: list[str]) -> bool:
        del capability_id, evidence_refs
        return False


def test_activation_controller_rejects_mismatched_release_evaluator_tenant() -> None:
    evaluator = ReleaseEvidenceEvaluator(
        {"registry_version": "1.0.0", "gates": []},
        "sha256:" + "a" * 64,
        _Disablement(),
        tenant_id="tenant-2",
    )

    with pytest.raises(ValueError, match="release evidence evaluator tenant mismatch"):
        ActivationController(_Connection(), tenant_id="tenant-1", evaluator=evaluator)


def test_activation_readback_returns_exact_payload_and_stays_inactive_without_evidence() -> None:
    payload = {
        "decisionId": "decision-1",
        "capabilityId": "email",
        "decision": "activate",
        "readbackRequired": True,
        "releaseDigest": "sha256:" + "a" * 64,
    }
    controller = ActivationController(_Connection([(payload,)]), tenant_id="tenant-1")
    assert controller.list_decisions() == [payload]
    assert controller.current_decision("email") == payload
    assert controller.capability_activated("email") is False
    empty = ActivationController(_Connection(), tenant_id="tenant-1")
    assert empty.list_decisions() == []
    assert empty.capability_activated("email") is False
