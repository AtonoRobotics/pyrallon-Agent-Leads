"""Governed e-signature provider bridge for representation onboarding."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

from .representation_runtime import ESignatureProvider, RepresentationRuntime


class ESignatureEffectInvoker(Protocol):
    def __call__(
        self,
        request: dict[str, Any],
        payload: bytes,
        *,
        permit_digest: str,
        preview: dict[str, Any] | None,
    ) -> dict[str, Any]: ...


class ConnectorESignatureProvider(ESignatureProvider):
    """Map governed connector receipts to the provider-neutral e-signature protocol."""

    def __init__(
        self,
        invoker: ESignatureEffectInvoker,
        *,
        request_for: Callable[[dict[str, Any], str, bytes], dict[str, Any]],
        permit_digest: str,
    ) -> None:
        if not permit_digest:
            raise ValueError("e-signature provider permit_digest is required")
        self._invoker = invoker
        self._request_for = request_for
        self._permit_digest = permit_digest

    def create_envelope(self, request: dict[str, Any]) -> dict[str, Any]:
        response = self._invoke(request, "esign.create", {**request, "action": "esign.create"})
        receipt = _receipt(response)
        return {
            "providerEnvelopeId": receipt,
            "evidenceId": receipt,
            "providerVersion": str(response.get("providerVersion") or "unknown"),
        }

    def get_envelope(self, provider_envelope_id: str) -> dict[str, Any]:
        if not provider_envelope_id:
            raise ValueError("provider_envelope_id is required")
        response = self._invoke(
            {"providerEnvelopeId": provider_envelope_id},
            "esign.get",
            {"action": "esign.get", "id": provider_envelope_id},
        )
        supplied_status = response.get("status")
        status = (
            str(supplied_status)
            if supplied_status in {"completed", "declined", "voided", "sent", "unknown"}
            else {
                "confirmed": "completed",
                "unknown": "unknown",
                "rejected": "declined",
                "conflict": "unknown",
            }.get(str(response.get("outcome") or ""), "unknown")
        )
        result: dict[str, Any] = {
            "status": status,
            "providerEnvelopeId": provider_envelope_id,
            "evidenceId": _receipt(response),
        }
        provider_response = response.get("providerResponse")
        if isinstance(provider_response, dict):
            for key in ("signatureEvidence", "executedArtifactId", "executedArtifactDigest"):
                if key in provider_response:
                    result[key] = provider_response[key]
        return result

    def _invoke(
        self, source: dict[str, Any], action: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        request = self._request_for(source, action, raw)
        return self._invoker(
            request,
            raw,
            permit_digest=self._permit_digest,
            preview=None,
        )


class ESignatureOperationService:
    """Run representation signature operations through the connector-backed provider."""

    def __init__(self, provider: ESignatureProvider) -> None:
        self._runtime = RepresentationRuntime(provider)

    def present(
        self,
        agreement_draft: dict[str, Any],
        *,
        agent_approved: bool,
        approval_digest: str | None,
    ) -> dict[str, Any]:
        return self._runtime.present_for_signature(
            agreement_draft,
            agent_approved=agent_approved,
            approval_digest=approval_digest,
        )

    def reconcile(
        self, agreement_draft: dict[str, Any], *, provider_envelope_id: str
    ) -> dict[str, Any]:
        return self._runtime.reconcile_signature(
            agreement_draft,
            provider_envelope_id=provider_envelope_id,
        )


def _receipt(response: dict[str, Any]) -> str:
    receipt = str(response.get("receiptId") or response.get("providerEnvelopeId") or "")
    if not receipt:
        raise ValueError("e-signature connector response has no receipt")
    return receipt
