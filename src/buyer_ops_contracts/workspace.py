"""Canonical operator workspace assembled from admitted records.

This is not operator-surface/1.1.0 JourneyView. JourneyView derivation rules
remain unpublished; this module reads BuyerJourney, Person, Appointment, and
related ontology records as stored.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .canonical_repository import CanonicalRepository
from .digest import sha256_digest
from .errors import ContractViolation
from .tenant_setup import SetupRejected, new_id

_CHANNEL_SOURCE = {
    "web_chat": "form",
    "email": "email",
    "sms": "sms",
    "phone": "referral",
    "in_person": "referral",
    "other": "form",
}


def _consultation_state(journey: dict[str, Any], appointments: list[dict[str, Any]]) -> str:
    if any(item.get("appointmentState") == "confirmed" for item in appointments):
        return "booked"
    if any(item.get("appointmentState") == "proposed" for item in appointments):
        return "offering"
    if journey.get("journeyState") == "consultation_ready":
        return "ready"
    if journey.get("journeyState") == "blocked":
        return "blocked"
    return "not_ready"


def _nurture_state(journey: dict[str, Any]) -> str:
    if journey.get("journeyState") == "nurture":
        return "active"
    if journey.get("journeyState") in {"dormant", "released"}:
        return "dormant"
    return "inactive"


def _person_endpoints(
    person: dict[str, Any], endpoints: dict[str, dict[str, Any]]
) -> dict[str, str | None]:
    email = None
    phone = None
    contactability = "unknown"
    inline = person.get("endpoints") or []
    for item in inline:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "email":
            email = str(item.get("normalizedValue") or "") or email
        if item.get("type") == "phone":
            phone = str(item.get("normalizedValue") or "") or phone
    for endpoint_id in person.get("endpointIds") or []:
        endpoint = endpoints.get(str(endpoint_id))
        if endpoint is None:
            continue
        if endpoint.get("endpointType") == "email":
            email = str(endpoint.get("normalizedValue") or "") or email
        if endpoint.get("endpointType") == "phone":
            phone = str(endpoint.get("normalizedValue") or "") or phone
        state = str(endpoint.get("contactabilityState") or "unknown")
        if state == "suppressed":
            contactability = "suppressed"
        elif contactability != "suppressed" and state == "contactable":
            contactability = "contactable"
        elif contactability == "unknown" and state:
            contactability = state
    for item in inline:
        if isinstance(item, dict) and item.get("status") == "suppressed":
            contactability = "suppressed"
    return {"email": email, "phone": phone, "contactability": contactability}


def _index(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in records}


def assemble_workspace(repository: CanonicalRepository) -> dict[str, Any]:
    tenants = repository.list_by_type("Tenant")
    brokerages = repository.list_by_type("Brokerage")
    holders = [
        item
        for item in repository.list_by_type("LicenseHolder")
        if item.get("status") == "active" and item.get("licenseState") == "active"
    ]
    people = repository.list_by_type("Person")
    endpoints = _index(repository.list_by_type("ContactEndpoint"))
    parties = repository.list_by_type("BuyingParty")
    journeys = repository.list_by_type("BuyerJourney")
    conversations = repository.list_by_type("Conversation")
    appointments = repository.list_by_type("Appointment")
    suppressions = [
        item
        for item in repository.list_by_type("Suppression")
        if item.get("validityState") == "active"
    ]
    people_by_id = _index(people)
    parties_by_id = _index(parties)
    appointments_by_journey: dict[str, list[dict[str, Any]]] = {}
    for appointment in appointments:
        appointments_by_journey.setdefault(str(appointment.get("journeyId")), []).append(
            appointment
        )
    conversations_by_journey: dict[str, dict[str, Any]] = {}
    for conversation in conversations:
        journey_id = conversation.get("primaryJourneyId")
        if isinstance(journey_id, str) and journey_id not in conversations_by_journey:
            conversations_by_journey[journey_id] = conversation
    suppressed_subjects = {str(item.get("subjectId")) for item in suppressions}

    holder = holders[0] if len(holders) == 1 else None
    holder_person = people_by_id.get(str(holder["personId"])) if holder else None
    brokerage = brokerages[0] if brokerages else None
    tenant = tenants[0] if tenants else None
    cards = []
    cases = []
    for journey in journeys:
        party = parties_by_id.get(str(journey.get("buyingPartyId")))
        member_id = None
        if party:
            members = party.get("members") or []
            if members:
                member_id = members[0].get("personId")
        person = people_by_id.get(str(member_id)) if member_id else None
        person_view = {
            "id": str(person["id"]) if person else "",
            "displayName": str(person.get("displayName") or journey["id"])
            if person
            else str(journey["id"]),
            "identityState": str(person.get("identityState") or "unknown") if person else "unknown",
            "email": None,
            "phone": None,
        }
        contactability = "unknown"
        if person is not None:
            endpoint_view = _person_endpoints(person, endpoints)
            person_view["email"] = endpoint_view["email"]
            person_view["phone"] = endpoint_view["phone"]
            contactability = str(endpoint_view["contactability"] or "unknown")
            if person["id"] in suppressed_subjects:
                contactability = "suppressed"
        journey_appointments = appointments_by_journey.get(str(journey["id"]), [])
        next_appointment = None
        proposed = [
            item for item in journey_appointments if item.get("appointmentState") == "proposed"
        ]
        if proposed:
            chosen = sorted(proposed, key=lambda item: str(item.get("startsAt") or ""))[0]
            next_appointment = {
                "id": chosen["id"],
                "startsAt": chosen.get("startsAt"),
                "state": chosen.get("appointmentState"),
                "locationOrMode": chosen.get("locationOrMode"),
            }
        current_conversation = conversations_by_journey.get(str(journey["id"]))
        source = "form"
        if current_conversation is not None:
            source = _CHANNEL_SOURCE.get(str(current_conversation.get("channel")), "form")
        open_cases = 1 if person_view["identityState"] in {"ambiguous", "conflict"} else 0
        if person is not None and person_view["identityState"] in {"ambiguous", "conflict"}:
            cases.append(
                {
                    "id": f"identity:{person['id']}",
                    "journeyId": journey["id"],
                    "kind": "identity",
                    "title": f"Identity {person_view['identityState']}",
                    "detail": "Person identityState is not resolved. Capture stays bound to this Person until an operator command admits a correction.",
                    "status": "open",
                    "createdAt": person.get("createdAt"),
                }
            )
        if journey.get("representationState") == "conflict":
            cases.append(
                {
                    "id": f"representation:{journey['id']}",
                    "journeyId": journey["id"],
                    "kind": "representation",
                    "title": "Representation conflict",
                    "detail": "BuyerJourney.representationState is conflict. Consultation readiness stays blocked until agreements on file are inspected.",
                    "status": "open",
                    "createdAt": journey.get("updatedAt"),
                }
            )
        cards.append(
            {
                "id": journey["id"],
                "personId": person_view["id"],
                "buyingPartyId": journey.get("buyingPartyId"),
                "journeyState": journey.get("journeyState"),
                "qualificationState": journey.get("qualificationState"),
                "representationState": journey.get("representationState"),
                "source": source,
                "sourceDetail": None,
                "serviceZone": journey.get("territory"),
                "contactability": contactability,
                "acknowledgment": "unknown",
                "consultationState": _consultation_state(journey, journey_appointments),
                "nurtureState": _nurture_state(journey),
                "blockerCodes": (
                    ["representation_conflict"]
                    if journey.get("representationState") == "conflict"
                    else []
                ),
                "createdAt": journey.get("createdAt"),
                "updatedAt": journey.get("updatedAt"),
                "person": person_view,
                "openCases": open_cases,
                "nextAppointment": next_appointment,
            }
        )
    appointment_views = [
        {
            "id": item["id"],
            "journeyId": item.get("journeyId"),
            "startsAt": item.get("startsAt"),
            "endsAt": item.get("endsAt"),
            "state": item.get("appointmentState"),
            "locationOrMode": item.get("locationOrMode"),
        }
        for item in appointments
    ]
    return {
        "projection": "canonical_records",
        "tenant": {
            "tenantId": tenant["id"] if tenant else repository._tenant_id,  # noqa: SLF001
            "brokerageName": brokerage.get("legalName") if brokerage else "",
            "agentName": holder_person.get("displayName") if holder_person else "",
            "licenseNumber": holder.get("licenseNumber") if holder else "",
            "licenseHolderId": holder["id"] if holder else "",
            "brokerageId": brokerage["id"] if brokerage else "",
            "jurisdiction": holder.get("jurisdiction") if holder else "",
        },
        "journeys": cards,
        "cases": cases,
        "appointments": appointment_views,
        "stats": {
            "active": len(
                [
                    item
                    for item in cards
                    if item["journeyState"] not in {"closed", "released", "ineligible"}
                ]
            ),
            "ready": len([item for item in cards if item["journeyState"] == "consultation_ready"]),
            "proposed": len([item for item in appointment_views if item["state"] == "proposed"]),
            "openCases": len(cases),
            "suppressed": len([item for item in cards if item["contactability"] == "suppressed"]),
        },
    }


def assemble_journey(repository: CanonicalRepository, journey_id: str) -> dict[str, Any]:
    workspace = assemble_workspace(repository)
    card = next((item for item in workspace["journeys"] if item["id"] == journey_id), None)
    journey = repository.get(journey_id)
    if journey is None or journey.get("recordType") != "BuyerJourney":
        raise KeyError("BuyerJourney not found")
    conversations = [
        item
        for item in repository.list_by_type("Conversation")
        if item.get("primaryJourneyId") == journey_id
    ]
    conversation_ids = {item["id"] for item in conversations}
    messages = [
        item
        for item in repository.list_by_type("Message")
        if item.get("conversationId") in conversation_ids
    ]
    observations = [
        item
        for item in repository.list_by_type("QualificationObservation")
        if item.get("journeyId") == journey_id
    ]
    assertions = _index(repository.list_by_type("Assertion"))
    criteria = _index(repository.list_by_type("QualificationCriterion"))
    appointments = [
        item
        for item in repository.list_by_type("Appointment")
        if item.get("journeyId") == journey_id
    ]
    consents = repository.list_by_type("ConsentGrant")
    commitments = [
        item
        for item in repository.list_by_type("Commitment")
        if item.get("journeyId") == journey_id
    ]
    person_id = card["personId"] if card else ""
    observation_views = []
    for item in observations:
        assertion = assertions.get(str(item.get("epistemicItemId")))
        criterion = criteria.get(str(item.get("criterionId")))
        value = ""
        if assertion is not None:
            proposition = assertion.get("proposition") or {}
            value = str(proposition.get("value") or "")
        observation_views.append(
            {
                "id": item["id"],
                "criterion": criterion.get("criterionCode")
                if criterion
                else item.get("criterionId"),
                "epistemicType": "assertion",
                "value": value,
                "observationState": item.get("observationState"),
                "sourceLabel": "operator",
            }
        )
    return {
        "projection": "canonical_records",
        "tenant": workspace["tenant"],
        "journey": card
        or {
            "id": journey["id"],
            "journeyState": journey.get("journeyState"),
            "qualificationState": journey.get("qualificationState"),
            "representationState": journey.get("representationState"),
        },
        "person": card["person"] if card else {"id": person_id, "displayName": journey_id},
        "messages": [
            {
                "id": item["id"],
                "direction": item.get("direction"),
                "channel": next(
                    (
                        conversation.get("channel")
                        for conversation in conversations
                        if conversation["id"] == item.get("conversationId")
                    ),
                    "form",
                ),
                "body": item.get("bodyArtifactId"),
                "deliveryState": item.get("deliveryState"),
                "createdAt": item.get("sentOrReceivedAt") or item.get("createdAt"),
            }
            for item in messages
        ],
        "observations": observation_views,
        "appointments": [
            {
                "id": item["id"],
                "journeyId": journey_id,
                "startsAt": item.get("startsAt"),
                "endsAt": item.get("endsAt"),
                "state": item.get("appointmentState"),
                "locationOrMode": item.get("locationOrMode"),
            }
            for item in appointments
        ],
        "cases": [item for item in workspace["cases"] if item.get("journeyId") == journey_id],
        "evidence": [],
        "consent": [
            {
                "id": item["id"],
                "channel": item.get("channel"),
                "purpose": item.get("purpose"),
                "status": item.get("validityState"),
                "basis": item.get("basis"),
            }
            for item in consents
            if item.get("personId") == person_id
        ],
        "commitments": [
            {
                "id": item["id"],
                "description": item.get("description") or item.get("id"),
                "state": item.get("commitmentState"),
                "dueAt": item.get("dueAt"),
            }
            for item in commitments
        ],
    }


def propose_appointment(
    repository: CanonicalRepository,
    *,
    journey_id: str,
    starts_at: str,
    actor_id: str,
    time_zone: str = "America/Chicago",
) -> dict[str, Any]:
    journey = repository.get(journey_id)
    if journey is None or journey.get("recordType") != "BuyerJourney":
        raise SetupRejected("validation_failed", "BuyerJourney not found")
    if journey.get("journeyState") != "consultation_ready":
        raise SetupRejected(
            "policy_denied",
            "a proposed consult requires BuyerJourney.journeyState consultation_ready",
        )
    holders = [
        item
        for item in repository.list_by_type("LicenseHolder")
        if item.get("status") == "active" and item.get("licenseState") == "active"
    ]
    if len(holders) != 1:
        raise SetupRejected(
            "configuration_incomplete", "exactly one active LicenseHolder is required"
        )
    parties = _index(repository.list_by_type("BuyingParty"))
    party = parties.get(str(journey.get("buyingPartyId")))
    if party is None or not party.get("members"):
        raise SetupRejected("validation_failed", "BuyingParty members are required")
    person_id = str(party["members"][0]["personId"])
    start = datetime.fromisoformat(starts_at.replace("Z", "+00:00")).astimezone(UTC)
    end = start + timedelta(minutes=45)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    evidence_id = new_id("evidence")
    appointment_id = new_id("appointment")
    digest = sha256_digest(
        {
            "journeyId": journey_id,
            "startsAt": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "actorId": actor_id,
        }
    )
    evidence = {
        "id": evidence_id,
        "tenantId": journey["tenantId"],
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "Evidence",
        "version": 1,
        "createdAt": stamp,
        "updatedAt": stamp,
        "effectiveFrom": stamp,
        "createdBy": {"actorType": "system_migration", "actorId": f"setup:{journey['tenantId']}"},
        "sourceEvidenceIds": [evidence_id],
        "status": "active",
        "sourceType": "manual_observation",
        "sourceRef": f"propose:{appointment_id}",
        "digest": digest,
        "retentionClass": "operational",
        "capturedAt": stamp,
        "evidenceState": "current",
    }
    appointment = {
        "id": appointment_id,
        "tenantId": journey["tenantId"],
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "Appointment",
        "version": 1,
        "createdAt": stamp,
        "updatedAt": stamp,
        "effectiveFrom": stamp,
        "createdBy": {"actorType": "system_migration", "actorId": f"setup:{journey['tenantId']}"},
        "sourceEvidenceIds": [evidence_id],
        "status": "active",
        "journeyId": journey_id,
        "appointmentType": "consultation",
        "participantIds": [person_id, holders[0]["id"]],
        "startsAt": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endsAt": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timeZone": time_zone,
        "locationOrMode": "proposed_local",
        "appointmentState": "proposed",
    }
    try:
        repository.save(evidence)
        saved = repository.save(appointment)
    except ContractViolation as exc:
        raise SetupRejected(
            "validation_failed",
            "; ".join(f"{item.code}: {item.message}" for item in exc.violations),
        ) from exc
    return saved


def record_assertion(
    repository: CanonicalRepository,
    *,
    journey_id: str,
    criterion_code: str,
    value: str,
    actor_id: str,
) -> dict[str, Any]:
    del actor_id
    journey = repository.get(journey_id)
    if journey is None:
        raise SetupRejected("validation_failed", "BuyerJourney not found")
    parties = _index(repository.list_by_type("BuyingParty"))
    party = parties.get(str(journey.get("buyingPartyId")))
    if party is None or not party.get("members"):
        raise SetupRejected("validation_failed", "BuyingParty members are required")
    person_id = str(party["members"][0]["personId"])
    criteria = [
        item
        for item in repository.list_by_type("QualificationCriterion")
        if item.get("criterionCode") == criterion_code and item.get("criterionState") == "active"
    ]
    if len(criteria) != 1:
        raise SetupRejected(
            "configuration_incomplete",
            f"exactly one active QualificationCriterion is required for {criterion_code}",
        )
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    evidence_id = new_id("evidence")
    assertion_id = new_id("assertion")
    observation_id = new_id("observation")
    digest = sha256_digest({"journeyId": journey_id, "criterion": criterion_code, "value": value})
    tenant_id = journey["tenantId"]
    evidence = {
        "id": evidence_id,
        "tenantId": tenant_id,
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "Evidence",
        "version": 1,
        "createdAt": stamp,
        "updatedAt": stamp,
        "effectiveFrom": stamp,
        "createdBy": {"actorType": "system_migration", "actorId": f"setup:{tenant_id}"},
        "sourceEvidenceIds": [evidence_id],
        "status": "active",
        "sourceType": "manual_observation",
        "sourceRef": assertion_id,
        "digest": digest,
        "retentionClass": "operational",
        "capturedAt": stamp,
        "evidenceState": "current",
    }
    assertion = {
        "id": assertion_id,
        "tenantId": tenant_id,
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "Assertion",
        "version": 1,
        "createdAt": stamp,
        "updatedAt": stamp,
        "effectiveFrom": stamp,
        "createdBy": {"actorType": "system_migration", "actorId": f"setup:{tenant_id}"},
        "sourceEvidenceIds": [evidence_id],
        "status": "active",
        "speakerType": "person",
        "speakerId": person_id,
        "sourceLocation": "operator_console",
        "proposition": {
            "subjectRef": journey_id,
            "predicate": criterion_code,
            "value": value,
            "applicableJourneyId": journey_id,
            "validFrom": stamp,
        },
        "assertionState": "current",
    }
    observation = {
        "id": observation_id,
        "tenantId": tenant_id,
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "QualificationObservation",
        "version": 1,
        "createdAt": stamp,
        "updatedAt": stamp,
        "effectiveFrom": stamp,
        "createdBy": {"actorType": "system_migration", "actorId": f"setup:{tenant_id}"},
        "sourceEvidenceIds": [evidence_id],
        "status": "active",
        "journeyId": journey_id,
        "criterionId": criteria[0]["id"],
        "epistemicItemId": assertion_id,
        "observationState": "asserted",
    }
    try:
        repository.save(evidence)
        repository.save(assertion)
        saved = repository.save(observation)
    except ContractViolation as exc:
        raise SetupRejected(
            "validation_failed",
            "; ".join(f"{item.code}: {item.message}" for item in exc.violations),
        ) from exc
    return saved


def record_suppression(
    repository: CanonicalRepository,
    *,
    journey_id: str,
    actor_id: str,
) -> dict[str, Any]:
    del actor_id
    journey = repository.get(journey_id)
    if journey is None:
        raise SetupRejected("validation_failed", "BuyerJourney not found")
    parties = _index(repository.list_by_type("BuyingParty"))
    party = parties.get(str(journey.get("buyingPartyId")))
    if party is None or not party.get("members"):
        raise SetupRejected("validation_failed", "BuyingParty members are required")
    person_id = str(party["members"][0]["personId"])
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    evidence_id = new_id("evidence")
    suppression_id = new_id("suppression")
    tenant_id = journey["tenantId"]
    digest = sha256_digest({"journeyId": journey_id, "personId": person_id, "reason": "opt_out"})
    evidence = {
        "id": evidence_id,
        "tenantId": tenant_id,
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "Evidence",
        "version": 1,
        "createdAt": stamp,
        "updatedAt": stamp,
        "effectiveFrom": stamp,
        "createdBy": {"actorType": "system_migration", "actorId": f"setup:{tenant_id}"},
        "sourceEvidenceIds": [evidence_id],
        "status": "active",
        "sourceType": "manual_observation",
        "sourceRef": suppression_id,
        "digest": digest,
        "retentionClass": "operational",
        "capturedAt": stamp,
        "evidenceState": "current",
    }
    suppression = {
        "id": suppression_id,
        "tenantId": tenant_id,
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "Suppression",
        "version": 1,
        "createdAt": stamp,
        "updatedAt": stamp,
        "effectiveFrom": stamp,
        "createdBy": {"actorType": "system_migration", "actorId": f"setup:{tenant_id}"},
        "sourceEvidenceIds": [evidence_id],
        "status": "active",
        "subjectId": person_id,
        "scope": "all_non_required_contact",
        "reason": "opt_out",
        "suppressedAt": stamp,
        "validityState": "active",
    }
    try:
        repository.save(evidence)
        saved = repository.save(suppression)
    except ContractViolation as exc:
        raise SetupRejected(
            "validation_failed",
            "; ".join(f"{item.code}: {item.message}" for item in exc.violations),
        ) from exc
    return saved
