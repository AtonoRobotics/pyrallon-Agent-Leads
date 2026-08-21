"""Durable representation onboarding decision derivation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .contract_acceptance import canonical_digest
from .structural import validate_record


class RepresentationOperationRuntime:
    def evaluate(
        self,
        *,
        journey: dict[str, Any],
        agreements: list[dict[str, Any]],
        iabs_deliveries: list[dict[str, Any]],
        now: datetime,
    ) -> dict[str, Any]:
        validate_record(journey, "ontology")
        now = now.astimezone(UTC)
        active_agreements = [
            item
            for item in agreements
            if item.get("recordType") == "WrittenBuyerAgreement"
            and item.get("status") in {"active", "current"}
        ]
        active_agreements.sort(key=lambda item: int(item.get("version", 0)))
        agreement = active_agreements[-1] if active_agreements else None
        agreement_state = str(agreement.get("executionState")) if agreement else ""
        delivery = any(
            item.get("recordType") == "IabsDelivery" and item.get("status") in {"active", "current"}
            for item in iabs_deliveries
        )
        if agreement_state in {"effective", "executed"}:
            state = "effective" if agreement_state == "effective" else "executed"
            preconditions = ["executed_artifact_evidence_present"]
        elif agreement_state in {"presented", "partially_signed"}:
            state = agreement_state
            preconditions = ["awaiting_buyer_signature"]
        elif agreement_state == "agent_approved":
            state = "presented"
            preconditions = ["approved_agreement_requires_esignature_provider"]
        elif agreement_state in {"expired", "terminated", "void", "superseded"}:
            state = "blocked"
            preconditions = [f"agreement_{agreement_state}"]
        else:
            state = "pending_agent_approval"
            preconditions = ["licensed_agent_approval_required"]
        if not delivery:
            preconditions.append("iabs_delivery_evidence_required")
        evidence_ids = sorted(
            {
                str(item["id"])
                for item in active_agreements + iabs_deliveries
                if isinstance(item.get("id"), str)
            }
            | {str(journey["id"])}
        )
        payload = {
            "journey": journey,
            "agreement": agreement,
            "iabsDeliveries": iabs_deliveries,
            "evidenceIds": evidence_ids,
        }
        result: dict[str, Any] = {
            "messageType": "representation_decision",
            "schemaVersion": "representation-operation/1.0.0",
            "tenantId": journey["tenantId"],
            "journeyId": journey["id"],
            "decisionId": f"representation:{journey['id']}:{journey['version']}",
            "representationState": state,
            "iabsNoticeRequired": not delivery,
            "preconditions": sorted(set(preconditions)),
            "agreementRef": (
                {
                    "recordId": agreement["id"],
                    "recordType": agreement["recordType"],
                    "version": int(agreement["version"]),
                }
                if agreement
                else None
            ),
            "derivedAt": now.isoformat().replace("+00:00", "Z"),
            "inputDigest": canonical_digest(payload),
            "evidenceIds": evidence_ids,
        }
        validate_record(result, "representation_operation")
        return result
