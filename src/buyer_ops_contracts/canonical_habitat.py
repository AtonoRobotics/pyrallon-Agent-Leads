"""Load Habitat evaluation state from canonical PostgreSQL records."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .habitat import HabitatState, PolicyDisposition
from .habitat_repository import Cursor, LockedHabitatStateReader


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


def _load_unique_matching(
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
    matches: list[dict[str, Any]] = []
    for row in cursor.fetchall():
        record = row[0]
        if isinstance(record, dict) and predicate(record):
            matches.append(record)
    return matches[0] if len(matches) == 1 else None


class CanonicalLockedHabitatStateReader(LockedHabitatStateReader):
    """Project current canonical authority into HabitatState under the resource lock."""

    def load_current(self, cursor: Cursor, intent: dict[str, Any]) -> HabitatState:
        tenant_id = intent["tenant_id"]
        records: dict[str, dict[str, Any]] = {}
        for record_id in intent["canonical_version_vector"]:
            loaded = _load_by_id(cursor, tenant_id, str(record_id))
            if loaded is not None:
                records[str(record_id)] = loaded
        principal = _load_by_id(cursor, tenant_id, intent["principal_id"])
        authorization = _load_unique_matching(
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
        workflow_reference = _load_unique_matching(
            cursor,
            tenant_id,
            "WorkflowReference",
            lambda rec: (
                rec.get("workflowId") == intent["workflow_id"]
                and rec.get("subjectId") == intent["buyer_journey_id"]
            ),
        )
        grant = _load_by_id(cursor, tenant_id, intent["connector_binding_id"])
        connector_view = consent = suppression = None
        del grant
        approval = None
        if "approval_ref" in intent:
            approval = _load_by_id(cursor, tenant_id, intent["approval_ref"])
        qualification = _load_unique_matching(
            cursor,
            tenant_id,
            "AgreementQualification",
            lambda rec: rec.get("actionIntentId") == intent["intent_id"],
        )
        agreement = None
        iabs_delivery = None
        if qualification is not None:
            agreement_id = qualification.get("agreementId")
            if agreement_id is not None:
                agreement = _load_by_id(cursor, tenant_id, str(agreement_id))
            delivery_id = qualification.get("iabsDeliveryId")
            if delivery_id is not None:
                iabs_delivery = _load_by_id(cursor, tenant_id, str(delivery_id))
        return HabitatState(
            records=records,
            principal=principal,
            authorization=authorization,
            workflow_reference=workflow_reference,
            approval=approval,
            connector_grant=connector_view,
            consent=consent,
            suppression=suppression,
            agreement_qualification=qualification,
            agreement=agreement,
            iabs_delivery=iabs_delivery,
        )


class PlatformPolicyEvaluator:
    """Fail-closed placeholder used until an owner policy evaluator is configured."""

    def evaluate(
        self,
        intent: dict[str, Any],
        state: HabitatState,
        evaluated_at: datetime,
    ) -> PolicyDisposition:
        del intent, state, evaluated_at
        return PolicyDisposition("prohibited", "policy-unavailable", "unconfigured")
