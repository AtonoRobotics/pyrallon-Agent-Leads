"""OT-01 form capture: inbound event → identity bundle → BuyerJourney."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .canonical_repository import CanonicalRepository
from .identity import IdentityRepository, identity_fingerprint
from .ingress import InboundEnvelope, RegisteredInboundEvent
from .journey_workflow import start_captured_journey

PURPOSE = "ot01.inbound"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


class CaptureIncomplete(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id(prefix: str) -> str:
    return f"{prefix}:{uuid.uuid4()}"


def classify_sender(sender: str) -> tuple[str, str]:
    value = sender.strip()
    if _EMAIL.fullmatch(value.lower()):
        return "email", value.lower()
    compact = re.sub(r"[^\d+]", "", value)
    if _E164.fullmatch(compact):
        return "phone", compact
    raise CaptureIncomplete(
        "validation_failed",
        "form senderEndpoint must be an email or an explicit E.164 phone number",
    )


def conversation_channel(envelope_channel: str) -> str:
    if envelope_channel == "form":
        return "web_chat"
    if envelope_channel in {"email", "sms"}:
        return envelope_channel
    raise CaptureIncomplete("validation_failed", "unsupported inbound channel for conversation")


def form_capture_records(
    *,
    tenant_id: str,
    holder: dict[str, Any],
    envelope: InboundEnvelope,
    registered: RegisteredInboundEvent,
    display_name: str,
    endpoint_type: str,
    normalized: str,
    stamp: str,
    evidence_id: str,
    person_id: str,
    endpoint_id: str,
    party_id: str,
    journey_id: str,
    conversation_id: str,
) -> dict[str, dict[str, Any]]:
    territory = holder.get("jurisdiction")
    if not isinstance(territory, str) or len(territory.strip()) < 2:
        raise CaptureIncomplete(
            "configuration_incomplete",
            "active LicenseHolder is missing jurisdiction",
        )
    actor = {"actorType": "license_holder", "actorId": holder["id"]}
    name = display_name.strip() or normalized
    evidence = {
        "id": evidence_id,
        "tenantId": tenant_id,
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "Evidence",
        "version": 1,
        "createdAt": stamp,
        "updatedAt": stamp,
        "effectiveFrom": stamp,
        "createdBy": actor,
        "sourceEvidenceIds": [evidence_id],
        "status": "active",
        "sourceType": "provider_receipt",
        "sourceRef": registered.event_id,
        "digest": envelope.payload_digest,
        "retentionClass": "operational",
        "capturedAt": stamp,
        "evidenceState": "current",
    }
    person = {
        "id": person_id,
        "tenantId": tenant_id,
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "Person",
        "version": 1,
        "createdAt": stamp,
        "updatedAt": stamp,
        "effectiveFrom": stamp,
        "createdBy": actor,
        "sourceEvidenceIds": [evidence_id],
        "status": "active",
        "identityState": "provisional",
        "displayName": name,
        "endpoints": [
            {
                "endpointId": endpoint_id,
                "type": endpoint_type,
                "normalizedValue": normalized,
                "verificationState": "unverified",
                "status": "active",
            }
        ],
    }
    endpoint = {
        "id": endpoint_id,
        "tenantId": tenant_id,
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "ContactEndpoint",
        "version": 1,
        "createdAt": stamp,
        "updatedAt": stamp,
        "effectiveFrom": stamp,
        "createdBy": actor,
        "sourceEvidenceIds": [evidence_id],
        "status": "active",
        "endpointType": endpoint_type,
        "normalizedValue": normalized,
        "ownerType": "person",
        "ownerId": person_id,
        "ownershipState": "asserted",
        "verificationState": "unverified",
        "contactabilityState": "unknown",
    }
    party = {
        "id": party_id,
        "tenantId": tenant_id,
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "BuyingParty",
        "version": 1,
        "createdAt": stamp,
        "updatedAt": stamp,
        "effectiveFrom": stamp,
        "createdBy": actor,
        "sourceEvidenceIds": [evidence_id],
        "status": "active",
        "members": [{"personId": person_id, "role": "buyer"}],
        "decisionAuthorityState": "unconfirmed",
    }
    journey = {
        "id": journey_id,
        "tenantId": tenant_id,
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "BuyerJourney",
        "version": 1,
        "createdAt": stamp,
        "updatedAt": stamp,
        "effectiveFrom": stamp,
        "createdBy": actor,
        "sourceEvidenceIds": [evidence_id],
        "status": "active",
        "buyingPartyId": party_id,
        "ownerLicenseHolderId": holder["id"],
        "territory": territory.strip(),
        "journeyState": "captured",
        "qualificationState": "not_started",
        "representationState": "unconfirmed",
    }
    conversation = {
        "id": conversation_id,
        "tenantId": tenant_id,
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "Conversation",
        "version": 1,
        "createdAt": stamp,
        "updatedAt": stamp,
        "effectiveFrom": stamp,
        "createdBy": actor,
        "sourceEvidenceIds": [evidence_id],
        "status": "active",
        "channel": conversation_channel(envelope.channel),
        "participants": [
            {"participantType": "person", "participantId": person_id, "role": "buyer"},
            {
                "participantType": "license_holder",
                "participantId": holder["id"],
                "role": "agent",
            },
        ],
        "primaryJourneyId": journey_id,
        "externalThreadRefs": {"ingress": registered.event_id},
        "conversationState": "open",
    }
    return {
        "evidence": evidence,
        "person": person,
        "endpoint": endpoint,
        "party": party,
        "journey": journey,
        "conversation": conversation,
    }


class FormCapture:
    """Create the OT-01 bundle only after inbound identity has been durably registered."""

    def __init__(
        self,
        connection: Any,
        *,
        tenant_id: str,
        start_journey: Callable[..., dict[str, str] | None] = start_captured_journey,
    ) -> None:
        self._connection = connection
        self._tenant_id = tenant_id
        self._canonical = CanonicalRepository(connection, tenant_id=tenant_id)
        self._identity = IdentityRepository(connection, tenant_id=tenant_id)
        self._start_journey = start_journey

    def after_ingress(
        self,
        envelope: InboundEnvelope,
        identity: dict[str, Any],
        registered: RegisteredInboundEvent,
        *,
        display_name: str,
    ) -> dict[str, Any]:
        endpoint_type, normalized = classify_sender(envelope.sender_endpoint)
        fingerprint = identity_fingerprint(
            identity_kind="provider_identity",
            normalized_identity=normalized,
            provider_account_ref=envelope.provider_account_ref,
            purpose=PURPOSE,
        )
        existing = self._identity.get_by_fingerprint(fingerprint)
        if existing is not None:
            journey_id = self._journey_for_person(existing.person_id)
            self._maybe_start(journey_id)
            return {
                "event_id": registered.event_id,
                "duplicate": registered.duplicate,
                "person_id": existing.person_id,
                "journey_id": journey_id,
                "mapping_id": existing.mapping_id,
            }
        holder = self._require_license_holder()
        stamp = identity.get("observedAt") or _now()
        records = form_capture_records(
            tenant_id=self._tenant_id,
            holder=holder,
            envelope=envelope,
            registered=registered,
            display_name=display_name,
            endpoint_type=endpoint_type,
            normalized=normalized,
            stamp=stamp,
            evidence_id=_id("evidence"),
            person_id=_id("person"),
            endpoint_id=_id("endpoint"),
            party_id=_id("party"),
            journey_id=_id("journey"),
            conversation_id=_id("conversation"),
        )
        mapping = self._identity.admit_created_bundle(
            canonical=self._canonical,
            evidence=records["evidence"],
            person=records["person"],
            endpoint=records["endpoint"],
            party=records["party"],
            journey=records["journey"],
            conversation=records["conversation"],
            mapping_id=_id("mapping"),
            identity_kind="provider_identity",
            normalized_identity=normalized,
            provider_account_ref=envelope.provider_account_ref,
            purpose=PURPOSE,
            resolution_basis="explicit_form_identity",
            evidence_ids=(records["evidence"]["id"],),
            effective_from=datetime.fromisoformat(stamp.replace("Z", "+00:00")),
            resolution_authority_ref=holder["id"],
        )
        journey_id = str(records["journey"]["id"])
        self._maybe_start(journey_id)
        return {
            "event_id": registered.event_id,
            "duplicate": False,
            "person_id": mapping.person_id,
            "journey_id": journey_id,
            "mapping_id": mapping.mapping_id,
        }

    def _maybe_start(self, journey_id: str | None) -> None:
        if not journey_id:
            return
        self._start_journey(tenant_id=self._tenant_id, journey_id=journey_id)

    def _require_license_holder(self) -> dict[str, Any]:
        holders = [
            item
            for item in self._canonical.list_by_type("LicenseHolder")
            if item.get("licenseState") == "active" and item.get("status") == "active"
        ]
        if len(holders) != 1:
            raise CaptureIncomplete(
                "configuration_incomplete",
                "exactly one active LicenseHolder is required before form capture can create a journey",
            )
        return holders[0]

    def _journey_for_person(self, person_id: str | None) -> str | None:
        if not person_id:
            return None
        parties = [
            party
            for party in self._canonical.list_by_type("BuyingParty")
            if any(member.get("personId") == person_id for member in party.get("members", []))
        ]
        party_ids = {party["id"] for party in parties}
        journeys = [
            journey
            for journey in self._canonical.list_by_type("BuyerJourney")
            if journey.get("buyingPartyId") in party_ids
        ]
        if not journeys:
            return None
        if len(journeys) != 1:
            raise CaptureIncomplete(
                "configuration_incomplete",
                "multiple BuyerJourneys match the resolved person; explicit journey binding is required",
            )
        return str(journeys[0]["id"])
