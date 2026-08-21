"""Representation onboarding and approved e-signature lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from .structural import validate_record


class ESignatureProvider(Protocol):
    def create_envelope(self, request: dict[str, Any]) -> dict[str, Any]: ...

    def get_envelope(self, provider_envelope_id: str) -> dict[str, Any]: ...


class RepresentationRuntimeError(ValueError):
    pass


def validate_written_buyer_agreement(
    agreement: dict[str, Any], *, now: datetime | None = None
) -> None:
    """Validate the safety-critical agreement invariants before canonical admission."""
    validate_record(agreement, "ontology")
    if agreement.get("recordType") != "WrittenBuyerAgreement":
        raise RepresentationRuntimeError("agreement_record_type_invalid")
    if agreement.get("status") != "active" or agreement.get("executionState") != "effective":
        raise RepresentationRuntimeError("agreement_not_effective")
    if agreement.get("jurisdiction") != "TX":
        raise RepresentationRuntimeError("unsupported_jurisdiction")
    if agreement.get("agreementType") == "non_representation_showing":
        effective = _time(agreement["effectiveAt"])
        terminates = _time(agreement["terminatesAt"])
        if terminates <= effective:
            raise RepresentationRuntimeError("agreement_term_invalid")
        if (terminates - effective).total_seconds() > 14 * 24 * 60 * 60:
            raise RepresentationRuntimeError("NON_REP_TERM_EXCEEDED")
        if agreement.get("exclusivity") != "non_exclusive":
            raise RepresentationRuntimeError("non_representation_must_be_non_exclusive")
        if any(item.get("allowed") is not True for item in agreement.get("serviceDefinitions", [])):
            raise RepresentationRuntimeError("service_definition_not_approved")
    compensation = agreement.get("compensation")
    if not isinstance(compensation, dict) or not compensation.get("objectivelyAscertainable"):
        raise RepresentationRuntimeError("compensation_disclosure_incomplete")
    if not compensation.get("negotiabilityDisclosurePresent"):
        raise RepresentationRuntimeError("compensation_negotiation_disclosure_missing")
    signatures = agreement.get("signatureEvidence")
    if not isinstance(signatures, list) or not signatures:
        raise RepresentationRuntimeError("signature_evidence_missing")
    if not agreement.get("executedArtifactId") or not str(
        agreement.get("executedArtifactDigest", "")
    ).startswith("sha256:"):
        raise RepresentationRuntimeError("executed_artifact_evidence_missing")
    if now is not None and _time(agreement["terminatesAt"]) <= now.astimezone(UTC):
        raise RepresentationRuntimeError("agreement_expired")


class RepresentationRuntime:
    """Own approval, e-signature transport, and provider truth reconciliation."""

    def __init__(
        self, provider: ESignatureProvider, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(UTC))

    def present_for_signature(
        self,
        agreement_draft: dict[str, Any],
        *,
        agent_approved: bool,
        approval_digest: str | None,
    ) -> dict[str, Any]:
        if not agent_approved or not approval_digest:
            raise RepresentationRuntimeError("agent_approval_required")
        if agreement_draft.get("agreementType") == "non_representation_showing":
            effective = _time(agreement_draft["effectiveAt"])
            terminates = _time(agreement_draft["terminatesAt"])
            if (terminates - effective).total_seconds() > 14 * 24 * 60 * 60:
                raise RepresentationRuntimeError("NON_REP_TERM_EXCEEDED")
        request = {
            "tenantId": agreement_draft["tenantId"],
            "agreementId": agreement_draft["id"],
            "agreementDigest": agreement_draft["executedArtifactDigest"],
            "approvalDigest": approval_digest,
            "templateId": agreement_draft.get("templateId", "approved-buyer-agreement"),
        }
        response = self._provider.create_envelope(request)
        envelope_id = response.get("providerEnvelopeId")
        if not isinstance(envelope_id, str) or not envelope_id:
            raise RepresentationRuntimeError("provider_envelope_id_missing")
        return {
            "state": "presented",
            "agreementId": agreement_draft["id"],
            "providerEnvelopeId": envelope_id,
            "approvalDigest": approval_digest,
            "providerEvidence": response.get("evidenceId"),
        }

    def reconcile_signature(
        self, agreement_draft: dict[str, Any], *, provider_envelope_id: str
    ) -> dict[str, Any]:
        response = self._provider.get_envelope(provider_envelope_id)
        status = response.get("status")
        if status not in {"completed", "declined", "voided", "sent", "unknown"}:
            raise RepresentationRuntimeError("provider_envelope_status_invalid")
        if status != "completed":
            return {
                "state": "pending" if status in {"sent", "unknown"} else "failed",
                "providerEnvelopeId": provider_envelope_id,
                "providerStatus": status,
            }
        agreement = dict(agreement_draft)
        agreement["executionState"] = "effective"
        agreement["status"] = "active"
        agreement["signatureEvidence"] = response.get("signatureEvidence", [])
        agreement["executedArtifactId"] = response.get(
            "executedArtifactId", agreement.get("executedArtifactId")
        )
        agreement["executedArtifactDigest"] = response.get(
            "executedArtifactDigest", agreement.get("executedArtifactDigest")
        )
        validate_written_buyer_agreement(agreement, now=self._clock())
        return {
            "state": "completed",
            "providerEnvelopeId": provider_envelope_id,
            "agreement": agreement,
            "providerEvidence": response.get("evidenceId"),
        }


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
