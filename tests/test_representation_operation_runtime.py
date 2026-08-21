from datetime import UTC, datetime

from buyer_ops_contracts.representation_operation_runtime import RepresentationOperationRuntime


def _journey() -> dict[str, object]:
    return {
        "id": "journey-1",
        "tenantId": "tenant-1",
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "BuyerJourney",
        "version": 1,
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
        "effectiveFrom": "2026-01-01T00:00:00Z",
        "createdBy": {"actorType": "person", "actorId": "agent-1"},
        "sourceEvidenceIds": ["evidence-journey"],
        "status": "active",
        "buyingPartyId": "party-1",
        "ownerLicenseHolderId": "agent-1",
        "territory": "service-area-1",
        "journeyState": "representation_pending",
        "qualificationState": "sufficient_for_consult",
        "representationState": "agreement_pending",
    }


def test_representation_runtime_requires_agent_approval_and_iabs_evidence() -> None:
    result = RepresentationOperationRuntime().evaluate(
        journey=_journey(), agreements=[], iabs_deliveries=[], now=datetime(2026, 8, 21, tzinfo=UTC)
    )
    assert result["representationState"] == "pending_agent_approval"
    assert result["iabsNoticeRequired"] is True
    assert "licensed_agent_approval_required" in result["preconditions"]
    assert "iabs_delivery_evidence_required" in result["preconditions"]


def test_representation_runtime_exposes_effective_agreement_state() -> None:
    agreement = {
        "id": "agreement-1",
        "recordType": "WrittenBuyerAgreement",
        "version": 2,
        "status": "active",
        "executionState": "effective",
    }
    delivery = {"id": "iabs-1", "recordType": "IabsDelivery", "status": "active"}
    result = RepresentationOperationRuntime().evaluate(
        journey=_journey(),
        agreements=[agreement],
        iabs_deliveries=[delivery],
        now=datetime(2026, 8, 21, tzinfo=UTC),
    )
    assert result["representationState"] == "effective"
    assert result["iabsNoticeRequired"] is False
    assert result["agreementRef"] == {
        "recordId": "agreement-1",
        "recordType": "WrittenBuyerAgreement",
        "version": 2,
    }
