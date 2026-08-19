"""Load Habitat evaluation state from canonical PostgreSQL records."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .habitat import HabitatState, PolicyDisposition
from .habitat_repository import Cursor, LockedHabitatStateReader

ACTION_CLASS_CAPABILITY = {
    "send_message": "send",
    "outbound_email": "send",
    "outbound_sms": "send",
    "outbound_acknowledgment": "send",
    "calendar_write": "schedule",
    "calendar_read": "read",
    "provider_write": "update",
}

PROHIBITED_ACTION_CLASSES = frozenset(
    {"outbound_ai_voice", "outbound_ai_voice_call", "autonomous_showing_selection"}
)


def _load_by_id(cursor: Cursor, tenant_id: str, record_id: str) -> dict[str, Any] | None:
    cursor.execute(
        "SELECT record FROM canonical_records_current "
        "WHERE tenant_id = %s AND record_id = %s FOR SHARE",
        (tenant_id, record_id),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    record = row[0]
    return record if isinstance(record, dict) else None


def _load_matching(
    cursor: Cursor,
    tenant_id: str,
    record_type: str,
    predicate: Any,
) -> dict[str, Any] | None:
    cursor.execute(
        "SELECT record FROM canonical_records_current "
        "WHERE tenant_id = %s AND record_type = %s FOR SHARE",
        (tenant_id, record_type),
    )
    for row in cursor.fetchall():
        record = row[0]
        if isinstance(record, dict) and predicate(record):
            return record
    return None


class CanonicalLockedHabitatStateReader(LockedHabitatStateReader):
    """Project current canonical authority into HabitatState under the resource lock."""

    def load_current(self, cursor: Cursor, intent: dict[str, Any]) -> HabitatState:
        tenant_id = intent["tenant_id"]
        principal = _load_by_id(cursor, tenant_id, intent["principal_id"])
        authorization = _load_matching(
            cursor,
            tenant_id,
            "Authorization",
            lambda rec: (
                rec.get("granteeId") == intent["principal_id"]
                and rec.get("actionClass") == intent["action_class"]
                and rec.get("resourceType") == intent["target_resource"]["resource_type"]
                and rec.get("resourceId") == intent["target_resource"]["resource_id"]
            ),
        )
        workflow_reference = _load_matching(
            cursor,
            tenant_id,
            "WorkflowReference",
            lambda rec: (
                rec.get("workflowId") == intent["workflow_id"]
                and rec.get("subjectId") == intent["buyer_journey_id"]
            ),
        )
        grant = _load_by_id(cursor, tenant_id, intent["connector_binding_id"])
        connector_view = None
        if grant is not None and grant.get("recordType") == "ConnectorGrant":
            capabilities = [str(item) for item in grant.get("capabilities", [])]
            required = ACTION_CLASS_CAPABILITY.get(intent["action_class"])
            connector_view = {
                "state": grant.get("grantState"),
                "connectorBindingId": grant.get("id"),
                "principalId": grant.get("delegatedPrincipalId"),
                "actionClasses": [intent["action_class"]] if required in capabilities else [],
                "requiresConsent": required == "send",
                "channel": next(
                    (
                        scope
                        for scope in grant.get("scopes", [])
                        if scope in {"email", "sms", "phone", "form", "calendar"}
                    ),
                    None,
                ),
            }
        approval = None
        if "approval_ref" in intent:
            approval = _load_by_id(cursor, tenant_id, intent["approval_ref"])
        recipient_id = intent["recipient"]["recipient_id"]
        suppression = _load_matching(
            cursor,
            tenant_id,
            "Suppression",
            lambda rec: rec.get("subjectId") == recipient_id
            and rec.get("validityState") == "active",
        )
        consent = _load_matching(
            cursor,
            tenant_id,
            "ConsentGrant",
            lambda rec: rec.get("personId") == recipient_id
            and rec.get("principalId") == intent["principal_id"]
            and rec.get("purpose") == intent["purpose"]
            and rec.get("validityState") == "active",
        )
        qualification = _load_matching(
            cursor,
            tenant_id,
            "AgreementQualification",
            lambda rec: rec.get("actionIntentId") == intent["intent_id"],
        )
        return HabitatState(
            records={},
            principal=principal,
            authorization=authorization,
            workflow_reference=workflow_reference,
            approval=approval,
            connector_grant=connector_view,
            consent=consent,
            suppression=suppression,
            agreement_qualification=qualification,
        )


class PlatformPolicyEvaluator:
    """Fail closed: platform prohibitions, else require an active Authorization already loaded."""

    def evaluate(
        self,
        intent: dict[str, Any],
        state: HabitatState,
        evaluated_at: datetime,
    ) -> PolicyDisposition:
        del evaluated_at
        if intent["action_class"] in PROHIBITED_ACTION_CLASSES:
            return PolicyDisposition("prohibited", "platform-invariant", "prd-7.1")
        if ACTION_CLASS_CAPABILITY.get(intent["action_class"]) is None:
            return PolicyDisposition("prohibited", "platform-invariant", "prd-7.1")
        if state.authorization is None:
            return PolicyDisposition("prohibited", "platform-invariant", "prd-7.1")
        return PolicyDisposition(
            "allowed",
            str(state.authorization.get("id", "authorization")),
            str(state.authorization.get("version", "1")),
        )
