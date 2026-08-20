from __future__ import annotations

from buyer_ops_contracts.authority_activation_fair_housing import (
    validate_authority_activation_fair_housing_semantics,
)
from buyer_ops_contracts.operator_contract import validate_operator_semantics
from buyer_ops_contracts.structural import validate_record
from buyer_ops_contracts.tenant_setup import SetupRejected, build_tenant_bundle
from buyer_ops_contracts.workspace import assemble_workspace


def _request() -> dict[str, str]:
    return {
        "tenantId": "brokerage-live-1",
        "legalName": "Atono Brokerage",
        "licenseNumber": "9001234",
        "licenseType": "sales_agent",
        "displayName": "Samuel",
        "operatorEmail": "samuel@pyrallon.local",
        "jurisdiction": "TX",
        "locale": "en-US",
        "emailProvider": "google_workspace",
        "calendarProvider": "google_workspace",
        "observedAt": "2020-01-01T00:00:00Z",
        "authorizationExpiresAt": "2040-01-01T00:00:00Z",
    }


def test_setup_bundle_admits_published_records() -> None:
    bundle = build_tenant_bundle(_request(), actor_id="actor-live-1")
    for record in bundle["records"]:
        validate_record(record, "ontology")
    validate_record(bundle["policy"], "operator_surface")
    validate_operator_semantics(bundle["policy"])
    validate_record(bundle["authorization"], "authority_activation_fair_housing")
    validate_authority_activation_fair_housing_semantics(bundle["authorization"])
    assert not any(item["recordType"] == "ConnectorGrant" for item in bundle["records"])
    assert bundle["authorization"]["role"] == "license_holder"
    assert bundle["authorization"]["actorId"] == "actor-live-1"


def test_setup_refuses_blank_tenant() -> None:
    request = _request()
    request["tenantId"] = "  "
    try:
        build_tenant_bundle(request, actor_id="actor-live-1")
    except SetupRejected as exc:
        assert "tenantId" in exc.detail
    else:
        raise AssertionError("blank tenant ids must be rejected")


class _Repo:
    def __init__(self, records: dict[str, list[dict]]) -> None:
        self._records = records
        self._tenant_id = "brokerage-live-1"

    def list_by_type(self, record_type: str) -> list[dict]:
        return list(self._records.get(record_type, []))


def test_workspace_reads_canonical_buyer_names() -> None:
    payload = assemble_workspace(
        _Repo(  # type: ignore[arg-type]
            {
                "Tenant": [{"id": "brokerage-live-1", "recordType": "Tenant"}],
                "Brokerage": [{"id": "brokerage:brokerage-live-1", "legalName": "Atono Brokerage"}],
                "LicenseHolder": [
                    {
                        "id": "license-holder:brokerage-live-1",
                        "personId": "person:holder:brokerage-live-1",
                        "licenseNumber": "9001234",
                        "jurisdiction": "TX",
                        "status": "active",
                        "licenseState": "active",
                    }
                ],
                "Person": [
                    {
                        "id": "person:holder:brokerage-live-1",
                        "displayName": "Samuel",
                        "identityState": "resolved",
                        "endpoints": [],
                    },
                    {
                        "id": "person-buyer-1",
                        "displayName": "Elena Vasquez",
                        "identityState": "provisional",
                        "endpoints": [
                            {
                                "endpointId": "ep-1",
                                "type": "email",
                                "normalizedValue": "elena@example.com",
                                "verificationState": "unverified",
                                "status": "active",
                            }
                        ],
                    },
                ],
                "ContactEndpoint": [],
                "BuyingParty": [
                    {
                        "id": "party-1",
                        "members": [{"personId": "person-buyer-1", "role": "buyer"}],
                    }
                ],
                "BuyerJourney": [
                    {
                        "id": "journey-1",
                        "buyingPartyId": "party-1",
                        "territory": "TX",
                        "journeyState": "captured",
                        "qualificationState": "not_started",
                        "representationState": "unconfirmed",
                        "createdAt": "2030-01-01T00:00:00Z",
                        "updatedAt": "2030-01-01T00:00:00Z",
                    }
                ],
                "Conversation": [
                    {
                        "id": "conv-1",
                        "primaryJourneyId": "journey-1",
                        "channel": "web_chat",
                    }
                ],
                "Appointment": [],
                "Suppression": [],
            }
        )
    )
    assert payload["projection"] == "canonical_records"
    assert payload["journeys"][0]["person"]["displayName"] == "Elena Vasquez"
    assert payload["journeys"][0]["source"] == "form"
    assert payload["stats"]["active"] == 1
