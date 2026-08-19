from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from buyer_ops_contracts import validate_closure_semantics, validate_record, validate_semantics
from buyer_ops_contracts.canonical_repository import CanonicalRepository
from buyer_ops_contracts.capture import (
    PURPOSE,
    CaptureIncomplete,
    FormCapture,
    classify_sender,
    form_capture_records,
)
from buyer_ops_contracts.identity import IdentityMapping, IdentityRepository, identity_fingerprint
from buyer_ops_contracts.ingress import InboundEnvelope, RegisteredInboundEvent
from buyer_ops_contracts.journey_workflow import start_captured_journey


def _envelope(**overrides: object) -> InboundEnvelope:
    payload = {
        "schemaVersion": "ot01.inbound/1",
        "providerEventId": "provider-event-1",
        "providerAccountRef": "form-source-1",
        "channel": "form",
        "receivedAt": "2026-08-19T12:00:00Z",
        "senderEndpoint": "buyer@example.com",
        "recipientEndpoint": "intake",
        "payloadArtifactId": "artifact-1",
        "payloadDigest": "sha256:" + "a" * 64,
        "signatureVerification": "not_supported",
    }
    payload.update(overrides)
    return InboundEnvelope.from_mapping(payload)


def _holder() -> dict:
    return {
        "id": "holder-1",
        "jurisdiction": "TX",
        "licenseState": "active",
        "status": "active",
    }


def test_classify_sender_normalizes_email_and_e164() -> None:
    assert classify_sender("Buyer@Example.COM") == ("email", "buyer@example.com")
    assert classify_sender("+1 (512) 555-1212") == ("phone", "+15125551212")


@pytest.mark.parametrize("sender", ["", "buyer", "5125551212", "+0123"])
def test_classify_sender_rejects_non_email_non_e164(sender: str) -> None:
    with pytest.raises(CaptureIncomplete) as raised:
        classify_sender(sender)
    assert raised.value.code == "validation_failed"


def test_form_capture_records_validate_against_ontology() -> None:
    records = form_capture_records(
        tenant_id="tenant-1",
        holder=_holder(),
        envelope=_envelope(),
        registered=RegisteredInboundEvent("inbound:1", duplicate=False, duplicate_of=None),
        display_name="Jordan Buyer",
        endpoint_type="email",
        normalized="buyer@example.com",
        stamp="2026-08-19T12:00:00Z",
        evidence_id="evidence-1",
        person_id="person-1",
        endpoint_id="endpoint-1",
        party_id="party-1",
        journey_id="journey-1",
        conversation_id="conversation-1",
    )
    assert records["evidence"]["sourceType"] == "provider_receipt"
    assert records["journey"]["journeyState"] == "captured"
    assert records["journey"]["territory"] == "TX"
    assert records["conversation"]["channel"] == "web_chat"
    for record in records.values():
        validate_record(record, "ontology")
        validate_semantics(record)


def test_form_capture_records_require_holder_jurisdiction() -> None:
    holder = _holder()
    holder["jurisdiction"] = ""
    with pytest.raises(CaptureIncomplete) as raised:
        form_capture_records(
            tenant_id="tenant-1",
            holder=holder,
            envelope=_envelope(),
            registered=RegisteredInboundEvent("inbound:1", duplicate=False, duplicate_of=None),
            display_name="Jordan Buyer",
            endpoint_type="email",
            normalized="buyer@example.com",
            stamp="2026-08-19T12:00:00Z",
            evidence_id="evidence-1",
            person_id="person-1",
            endpoint_id="endpoint-1",
            party_id="party-1",
            journey_id="journey-1",
            conversation_id="conversation-1",
        )
    assert raised.value.code == "configuration_incomplete"


def test_form_external_message_identity_validates_against_closure() -> None:
    digest = "sha256:" + "a" * 64
    identity = {
        "schemaVersion": "open-019-024/1.1.0",
        "tenantId": "tenant-1",
        "recordId": "event-1",
        "recordVersion": 1,
        "observedAt": "2026-08-19T12:00:00Z",
        "effectiveFrom": "2026-08-19T12:00:00Z",
        "status": "current",
        "evidenceRefs": ["artifact-1"],
        "recordType": "ExternalMessageIdentity",
        "connectorId": "form.local",
        "provider": "form",
        "providerAccountRef": "form-source-1",
        "externalMessageId": "event-1",
        "externalEventId": "event-1",
        "payloadDigest": digest,
    }
    validate_record(identity, "closure")
    validate_closure_semantics(identity)


def test_after_ingress_rejects_invalid_sender_before_persistence() -> None:
    capture = FormCapture(object(), tenant_id="tenant-1")
    with pytest.raises(CaptureIncomplete) as raised:
        capture.after_ingress(
            replace(_envelope(), sender_endpoint="not-an-endpoint"),
            {"observedAt": "2026-08-19T12:00:00Z"},
            RegisteredInboundEvent("inbound:1", duplicate=False, duplicate_of=None),
            display_name="Jordan Buyer",
        )
    assert raised.value.code == "validation_failed"


def _mapping() -> IdentityMapping:
    return IdentityMapping(
        tenant_id="tenant-1",
        identity_fingerprint=identity_fingerprint(
            identity_kind="provider_identity",
            normalized_identity="buyer@example.com",
            provider_account_ref="form-source-1",
            purpose=PURPOSE,
        ),
        mapping_id="mapping-1",
        version=1,
        identity_kind="provider_identity",
        normalized_identity="buyer@example.com",
        provider_account_ref="form-source-1",
        purpose=PURPOSE,
        resolution_basis="explicit_form_identity",
        resolution_authority_ref="holder-1",
        outcome="created",
        person_id="person-1",
        person_version=1,
        resolution_case_id=None,
        candidate_person_ids=(),
        evidence_ids=("evidence-1",),
        effective_from=datetime(2026, 8, 19, 12, tzinfo=UTC),
    )


def test_after_ingress_reuses_person_and_journey_for_matching_fingerprint() -> None:
    starts: list[dict] = []
    mapping = _mapping()

    def list_by_type(record_type: str) -> list[dict]:
        if record_type == "BuyingParty":
            return [{"id": "party-1", "members": [{"personId": "person-1"}]}]
        if record_type == "BuyerJourney":
            return [
                {
                    "id": "journey-existing",
                    "buyingPartyId": "party-1",
                    "createdAt": "2026-08-19T12:00:00Z",
                }
            ]
        raise AssertionError(f"unexpected list_by_type({record_type})")

    with (
        patch.object(IdentityRepository, "get_by_fingerprint", return_value=mapping),
        patch.object(CanonicalRepository, "list_by_type", side_effect=list_by_type),
        patch.object(IdentityRepository, "admit_created_bundle") as admit,
    ):
        result = FormCapture(
            object(),
            tenant_id="tenant-1",
            start_journey=lambda **kwargs: starts.append(kwargs) or None,
        ).after_ingress(
            _envelope(),
            {"observedAt": "2026-08-19T12:00:00Z"},
            RegisteredInboundEvent("inbound:1", duplicate=True, duplicate_of="inbound:1"),
            display_name="Jordan Buyer",
        )
    admit.assert_not_called()
    assert result["person_id"] == "person-1"
    assert result["journey_id"] == "journey-existing"
    assert result["mapping_id"] == "mapping-1"
    assert starts == [{"tenant_id": "tenant-1", "journey_id": "journey-existing"}]


@pytest.mark.parametrize(
    "holders",
    [
        [],
        [_holder(), {**_holder(), "id": "holder-2"}],
        [{**_holder(), "licenseState": "inactive"}],
    ],
)
def test_after_ingress_fails_closed_without_exactly_one_active_license_holder(
    holders: list[dict],
) -> None:
    with (
        patch.object(IdentityRepository, "get_by_fingerprint", return_value=None),
        patch.object(CanonicalRepository, "list_by_type", return_value=holders),
        patch.object(IdentityRepository, "admit_created_bundle") as admit,
        pytest.raises(CaptureIncomplete) as raised,
    ):
        FormCapture(object(), tenant_id="tenant-1", start_journey=lambda **_: None).after_ingress(
            _envelope(),
            {"observedAt": "2026-08-19T12:00:00Z"},
            RegisteredInboundEvent("inbound:1", duplicate=False, duplicate_of=None),
            display_name="Jordan Buyer",
        )
    assert raised.value.code == "configuration_incomplete"
    admit.assert_not_called()


def test_new_capture_starts_temporal_and_does_not_write_workflow_reference() -> None:
    starts: list[dict] = []
    saves: list[dict] = []
    mapping = _mapping()

    def list_by_type(record_type: str) -> list[dict]:
        if record_type == "LicenseHolder":
            return [_holder()]
        raise AssertionError(f"unexpected list_by_type({record_type})")

    with (
        patch.object(IdentityRepository, "get_by_fingerprint", return_value=None),
        patch.object(CanonicalRepository, "list_by_type", side_effect=list_by_type),
        patch.object(IdentityRepository, "admit_created_bundle", return_value=mapping),
        patch.object(CanonicalRepository, "save", side_effect=lambda record, **_: saves.append(record) or record),
    ):
        result = FormCapture(
            object(),
            tenant_id="tenant-1",
            start_journey=lambda **kwargs: starts.append(kwargs) or None,
        ).after_ingress(
            _envelope(),
            {"observedAt": "2026-08-19T12:00:00Z"},
            RegisteredInboundEvent("inbound:1", duplicate=False, duplicate_of=None),
            display_name="Jordan Buyer",
        )
    assert result["person_id"] == mapping.person_id
    assert result["journey_id"]
    assert starts == [{"tenant_id": "tenant-1", "journey_id": result["journey_id"]}]
    assert all(item.get("recordType") != "WorkflowReference" for item in saves)


def test_unconfigured_temporal_does_not_start_or_synthesize_a_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TEMPORAL_ADDRESS", raising=False)
    monkeypatch.delenv("TEMPORAL_RUNTIME_POLICY_JSON", raising=False)
    monkeypatch.delenv("TEMPORAL_TASK_QUEUE", raising=False)
    assert start_captured_journey(tenant_id="tenant-1", journey_id="journey-1") is None
