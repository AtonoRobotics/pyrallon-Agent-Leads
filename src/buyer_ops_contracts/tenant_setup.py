"""OPEN-001 / OPEN-003 tenant admission from operator-supplied identity.

No tenant id, license number, or connector binding is invented. Grants are
admitted as pending. Live email, SMS, and calendar stay inactive.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from .actor_authorization import ActorTenantAuthorizationRepository
from .canonical_repository import CanonicalRepository
from .digest import sha256_digest
from .errors import ContractViolation
from .operator_policy import OperatorPolicyRepository

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LICENSE_TYPES = frozenset({"broker", "associated_broker", "sales_agent"})
_COMMAND_RULES = [
    {
        "command_type": "approve",
        "action_class": "approve",
        "target_record_types": ["Approval"],
        "actor_types": ["license_holder"],
    },
    {
        "command_type": "deny",
        "action_class": "deny",
        "target_record_types": ["Approval"],
        "actor_types": ["license_holder"],
    },
    {
        "command_type": "correct_replace",
        "action_class": "correct_epistemic_item",
        "target_record_types": ["Assertion", "VerifiedFact", "Inference", "Memory"],
        "actor_types": ["license_holder"],
    },
    {
        "command_type": "correct_invalidate",
        "action_class": "correct_epistemic_item",
        "target_record_types": ["Assertion", "VerifiedFact", "Inference", "Memory"],
        "actor_types": ["license_holder"],
    },
    {
        "command_type": "revoke_authorization",
        "action_class": "revoke_authorization",
        "target_record_types": ["Authorization"],
        "actor_types": ["license_holder"],
    },
    {
        "command_type": "revoke_approval",
        "action_class": "revoke_approval",
        "target_record_types": ["Approval"],
        "actor_types": ["license_holder"],
    },
    {
        "command_type": "pause_workflow",
        "action_class": "pause_workflow",
        "target_record_types": ["WorkflowReference"],
        "actor_types": ["license_holder"],
    },
    {
        "command_type": "resume_workflow",
        "action_class": "resume_workflow",
        "target_record_types": ["WorkflowReference"],
        "actor_types": ["license_holder"],
    },
    {
        "command_type": "request_reconciliation",
        "action_class": "request_reconciliation",
        "target_record_types": ["EffectAttempt"],
        "actor_types": ["license_holder"],
    },
]
QUALIFICATION_CRITERIA = (
    ("identity", "Identity"),
    ("representation", "Existing representation"),
    ("purchase_intent", "Purchase intent"),
    ("geography", "Target geography"),
    ("property", "Property needs"),
    ("timing", "Timing"),
    ("budget_financing", "Budget and financing"),
    ("contingency", "Sale contingency"),
    ("decision_participants", "Decision participants"),
    ("scheduling", "Scheduling"),
    ("channel", "Preferred channel"),
)


class SetupRejected(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require(field: str, value: str) -> str:
    text = value.strip()
    if not text:
        raise SetupRejected("validation_failed", f"{field} is required")
    return text


def _canonical(
    *,
    record_id: str,
    tenant_id: str,
    record_type: str,
    stamp: str,
    evidence_id: str,
    **fields: Any,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": record_id,
        "tenantId": tenant_id,
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": record_type,
        "version": 1,
        "createdAt": stamp,
        "updatedAt": stamp,
        "effectiveFrom": stamp,
        "createdBy": {"actorType": "system_migration", "actorId": f"setup:{tenant_id}"},
        "sourceEvidenceIds": [evidence_id],
        "status": "active",
    }
    record.update(fields)
    return record


def build_tenant_bundle(request: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
    tenant_id = _require("tenantId", str(request.get("tenantId") or ""))
    legal_name = _require("legalName", str(request.get("legalName") or ""))
    license_number = _require("licenseNumber", str(request.get("licenseNumber") or ""))
    license_type = _require("licenseType", str(request.get("licenseType") or ""))
    if license_type not in _LICENSE_TYPES:
        raise SetupRejected("validation_failed", "licenseType is not a published license type")
    display_name = _require("displayName", str(request.get("displayName") or ""))
    operator_email = _require("operatorEmail", str(request.get("operatorEmail") or "")).lower()
    if not _EMAIL.fullmatch(operator_email):
        raise SetupRejected("validation_failed", "operatorEmail must be an email address")
    jurisdiction = _require("jurisdiction", str(request.get("jurisdiction") or ""))
    if len(jurisdiction) < 2:
        raise SetupRejected("validation_failed", "jurisdiction is required")
    locale = _require("locale", str(request.get("locale") or "en-US"))
    actor = _require("actorId", actor_id)
    stamp = str(request.get("observedAt") or _now())
    expires_at = str(
        request.get("authorizationExpiresAt")
        or _stamp(datetime.now(UTC) + timedelta(days=3650))
    )
    evidence_id = f"evidence:setup:{tenant_id}"
    policy_id = f"operator-policy:{tenant_id}"
    policy_profile_id = f"policy-profile:{tenant_id}"
    brokerage_id = f"brokerage:{tenant_id}"
    person_id = f"person:holder:{tenant_id}"
    endpoint_id = f"endpoint:holder:{tenant_id}"
    holder_id = f"license-holder:{tenant_id}"
    grant_id = f"actor-auth:{tenant_id}:{actor}"
    digest = sha256_digest(
        {
            "tenantId": tenant_id,
            "legalName": legal_name,
            "licenseNumber": license_number,
            "actorId": actor,
            "operatorEmail": operator_email,
        }
    )
    evidence = _canonical(
        record_id=evidence_id,
        tenant_id=tenant_id,
        record_type="Evidence",
        stamp=stamp,
        evidence_id=evidence_id,
        sourceType="manual_observation",
        sourceRef=f"setup:{tenant_id}",
        digest=digest,
        retentionClass="operational",
        capturedAt=stamp,
        evidenceState="current",
    )
    tenant = _canonical(
        record_id=tenant_id,
        tenant_id=tenant_id,
        record_type="Tenant",
        stamp=stamp,
        evidence_id=evidence_id,
        deploymentMode="dedicated_brokerage",
        locale=locale,
        policyProfileId=policy_profile_id,
        tenantState="active",
    )
    brokerage = _canonical(
        record_id=brokerage_id,
        tenant_id=tenant_id,
        record_type="Brokerage",
        stamp=stamp,
        evidence_id=evidence_id,
        legalName=legal_name,
        licenseNumber=license_number,
        jurisdiction=jurisdiction,
        policyProfileId=policy_profile_id,
        brokerageState="active",
    )
    person = _canonical(
        record_id=person_id,
        tenant_id=tenant_id,
        record_type="Person",
        stamp=stamp,
        evidence_id=evidence_id,
        identityState="resolved",
        displayName=display_name,
        endpoints=[
            {
                "endpointId": endpoint_id,
                "type": "email",
                "normalizedValue": operator_email,
                "verificationState": "unverified",
                "status": "active",
            }
        ],
    )
    endpoint = _canonical(
        record_id=endpoint_id,
        tenant_id=tenant_id,
        record_type="ContactEndpoint",
        stamp=stamp,
        evidence_id=evidence_id,
        endpointType="email",
        normalizedValue=operator_email,
        ownerType="person",
        ownerId=person_id,
        ownershipState="asserted",
        verificationState="unverified",
        contactabilityState="unknown",
    )
    holder = _canonical(
        record_id=holder_id,
        tenant_id=tenant_id,
        record_type="LicenseHolder",
        stamp=stamp,
        evidence_id=evidence_id,
        personId=person_id,
        licenseNumber=license_number,
        licenseType=license_type,
        jurisdiction=jurisdiction,
        sponsoringBrokerageId=brokerage_id,
        activeFrom=stamp,
        licenseState="active",
    )
    criteria = [
        _canonical(
            record_id=f"criterion:{code}:{tenant_id}",
            tenant_id=tenant_id,
            record_type="QualificationCriterion",
            stamp=stamp,
            evidence_id=evidence_id,
            criterionCode=code,
            purpose=purpose,
            allowedObservationStates=[
                "unknown",
                "buyer_declined",
                "asserted",
                "verified",
                "inferred",
                "stale",
                "contradicted",
                "not_applicable",
            ],
            freshnessSeconds=2_592_000,
            criterionState="active",
        )
        for code, purpose in QUALIFICATION_CRITERIA
    ]
    policy = {
        "message_type": "operator_policy",
        "schema_version": "operator-surface/1.1.0",
        "policy_id": policy_id,
        "tenant_id": tenant_id,
        "record_version": 1,
        "effective_from": stamp,
        "status": "active",
        "command_rules": _COMMAND_RULES,
        "evidence_refs": [
            {
                "record_id": evidence_id,
                "record_type": "Evidence",
                "version": 1,
                "digest": digest,
                "captured_at": stamp,
            }
        ],
    }
    authorization = {
        "schemaVersion": "open-025-027/1.0.0",
        "recordType": "ActorTenantAuthorization",
        "tenantId": tenant_id,
        "recordId": grant_id,
        "observedAt": stamp,
        "actorId": actor,
        "principalId": holder_id,
        "role": "license_holder",
        "allowedCommands": [rule["command_type"] for rule in _COMMAND_RULES],
        "recordScopes": [
            "BuyerJourney",
            "Person",
            "Appointment",
            "EffectAttempt",
            "Assertion",
            "Authorization",
            "Approval",
            "WorkflowReference",
        ],
        "policyVersion": policy_id,
        "authorizationVersion": 1,
        "effectiveAt": stamp,
        "expiresAt": expires_at,
        "status": "active",
    }
    return {
        "tenantId": tenant_id,
        "brokerageId": brokerage_id,
        "licenseHolderId": holder_id,
        "personId": person_id,
        "policyId": policy_id,
        "authorizationId": grant_id,
        "evidence": evidence,
        "records": [evidence, tenant, brokerage, person, endpoint, holder, *criteria],
        "policy": policy,
        "authorization": authorization,
    }


def bootstrap_tenant(connection: Any, request: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
    bundle = build_tenant_bundle(request, actor_id=actor_id)
    tenant_id = str(bundle["tenantId"])
    canonical = CanonicalRepository(connection, tenant_id=tenant_id)
    existing = canonical.get(tenant_id)
    if existing is not None:
        grants = ActorTenantAuthorizationRepository(
            connection, tenant_id=tenant_id
        ).list_current_for_actor(actor_id)
        if not grants:
            raise SetupRejected(
                "authority_denied",
                "tenant already exists and this actor has no current authorization",
            )
        return {
            "tenantId": tenant_id,
            "brokerageId": bundle["brokerageId"],
            "licenseHolderId": bundle["licenseHolderId"],
            "personId": bundle["personId"],
            "policyId": bundle["policyId"],
            "authorizationId": grants[0]["recordId"],
            "idempotent": True,
        }
    try:
        for record in bundle["records"]:
            canonical.save(record)
        OperatorPolicyRepository(connection, tenant_id=tenant_id).admit(bundle["policy"])
        ActorTenantAuthorizationRepository(connection, tenant_id=tenant_id).save(
            bundle["authorization"]
        )
    except ContractViolation as exc:
        raise SetupRejected(
            "validation_failed",
            "; ".join(f"{item.code}: {item.message}" for item in exc.violations),
        ) from exc
    return {
        "tenantId": tenant_id,
        "brokerageId": bundle["brokerageId"],
        "licenseHolderId": bundle["licenseHolderId"],
        "personId": bundle["personId"],
        "policyId": bundle["policyId"],
        "authorizationId": bundle["authorizationId"],
        "idempotent": False,
    }


def new_id(prefix: str) -> str:
    return f"{prefix}:{uuid.uuid4()}"
