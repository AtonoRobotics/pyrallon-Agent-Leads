import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from buyer_ops_contracts.esignature_operations import ConnectorESignatureProvider
from buyer_ops_contracts.representation_runtime import (
    RepresentationRuntime,
    RepresentationRuntimeError,
    validate_written_buyer_agreement,
)

ROOT = Path(__file__).resolve().parents[1]


def _agreement() -> dict:
    return json.loads((ROOT / "tests/fixtures/valid/written_buyer_agreement.json").read_text())


class _Provider:
    def create_envelope(self, request):
        return {"providerEnvelopeId": "env-1", "evidenceId": "provider-evidence-1"}

    def get_envelope(self, _provider_envelope_id):
        return {
            "status": "completed",
            "signatureEvidence": [
                {
                    "signerPartyId": "party-1",
                    "signedAt": "2030-01-01T23:00:00Z",
                    "evidenceId": "evidence-1",
                }
            ],
            "executedArtifactId": "artifact-1",
            "executedArtifactDigest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "evidenceId": "provider-evidence-2",
        }


def test_non_representation_term_is_fail_closed() -> None:
    agreement = _agreement()
    agreement["terminatesAt"] = "2030-01-17T00:00:01Z"
    with pytest.raises(RepresentationRuntimeError, match="NON_REP_TERM_EXCEEDED"):
        validate_written_buyer_agreement(agreement)


def test_signature_requires_agent_approval_and_reconciles_provider_truth() -> None:
    runtime = RepresentationRuntime(_Provider(), clock=lambda: datetime(2030, 1, 3, tzinfo=UTC))
    agreement = _agreement()
    with pytest.raises(RepresentationRuntimeError, match="agent_approval_required"):
        runtime.present_for_signature(agreement, agent_approved=False, approval_digest=None)
    presented = runtime.present_for_signature(
        agreement, agent_approved=True, approval_digest="sha256:approval"
    )
    completed = runtime.reconcile_signature(
        copy.deepcopy(agreement), provider_envelope_id=presented["providerEnvelopeId"]
    )
    assert presented["state"] == "presented"
    assert completed["state"] == "completed"


def test_connector_esignature_provider_maps_receipts_without_exposing_credentials() -> None:
    calls: list[tuple[dict, bytes, str]] = []

    def invoke(request, payload, *, permit_digest, preview):
        calls.append((request, payload, permit_digest))
        return {
            "outcome": "confirmed",
            "receiptId": "envelope-1",
            "providerVersion": "docusign-v1",
            "providerResponse": {
                "status": "completed",
                "signatureEvidence": [{"signerPartyId": "party-1"}],
                "executedArtifactId": "artifact-1",
                "executedArtifactDigest": "sha256:" + "a" * 64,
                "authorization": "must-not-cross-boundary",
            },
        }

    provider = ConnectorESignatureProvider(
        invoke,
        request_for=lambda source, action, raw: {
            "tenantId": source.get("tenantId", "tenant-1"),
            "capability": action,
            "payloadDigest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        },
        permit_digest="permit-1",
    )
    created = provider.create_envelope({"tenantId": "tenant-1", "agreementId": "agreement-1"})
    assert created["providerEnvelopeId"] == "envelope-1"
    assert calls[0][2] == "permit-1"
    assert b"permit-1" not in calls[0][1]
    observed = provider.get_envelope("envelope-1")
    assert observed["status"] == "completed"
    assert observed["executedArtifactId"] == "artifact-1"
    assert "authorization" not in observed
