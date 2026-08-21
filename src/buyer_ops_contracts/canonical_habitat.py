"""Load Habitat evaluation state from canonical PostgreSQL records."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
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

    def __init__(
        self,
        inventory_verifier: Callable[[dict[str, Any]], bool] | None = None,
        activation_verifier: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        self._inventory_verifier = inventory_verifier
        self._activation_verifier = activation_verifier

    @staticmethod
    def _load_closure_by_id(
        cursor: Cursor, tenant_id: str, record_id: str, record_type: str
    ) -> dict[str, Any] | None:
        cursor.execute(
            "SELECT payload FROM closure_records_current "
            "WHERE tenant_id = %s AND record_id = %s AND record_type = %s FOR SHARE",
            (tenant_id, record_id, record_type),
        )
        row = cursor.fetchone()
        payload = None if row is None else row[0]
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _load_activation(
        cursor: Cursor, tenant_id: str, capability_id: str
    ) -> dict[str, Any] | None:
        cursor.execute(
            "SELECT payload FROM release_activation_decisions "
            "WHERE tenant_id = %s AND capability_id = %s "
            "ORDER BY activation_version DESC LIMIT 1 FOR SHARE",
            (tenant_id, capability_id),
        )
        row = cursor.fetchone()
        payload = None if row is None else row[0]
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _load_effect_policy(
        cursor: Cursor, tenant_id: str, action_class: str, evaluated_at: datetime
    ) -> dict[str, Any] | None:
        """Load exactly one current policy rule for the requested effect class.

        Policy is closure state, not request input.  Ambiguous, stale, revoked,
        or malformed policy remains unavailable and therefore denied by the
        evaluator below.
        """
        cursor.execute(
            "SELECT payload FROM closure_records_current "
            "WHERE tenant_id = %s AND record_type = 'EffectPolicy' FOR SHARE",
            (tenant_id,),
        )
        now = evaluated_at.astimezone(UTC)
        matches: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            policy = row[0]
            if not isinstance(policy, dict) or policy.get("status") != "current":
                continue
            try:
                effective_from = _timestamp(str(policy["effectiveFrom"]))
                expires_at = _timestamp(str(policy["expiresAt"]))
            except (KeyError, TypeError, ValueError):
                continue
            if effective_from > now or expires_at <= now:
                continue
            rules = policy.get("rules")
            if not isinstance(rules, list):
                continue
            action_rules = [
                rule
                for rule in rules
                if isinstance(rule, dict) and rule.get("actionClass") == action_class
            ]
            if len(action_rules) == 1:
                matches.append({**policy, "selectedRule": action_rules[0]})
        return matches[0] if len(matches) == 1 else None

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
        context = intent["effect_context"]
        grant = _load_by_id(cursor, tenant_id, context["grant_id"])
        inventory = self._load_closure_by_id(
            cursor, tenant_id, context["inventory_record_id"], "CapabilityInventory"
        )
        preview = self._load_closure_by_id(
            cursor, tenant_id, context["draft_preview_record_id"], "EffectDraftPreview"
        )
        activation = self._load_activation(cursor, tenant_id, context["capability_id"])
        effect_policy = self._load_effect_policy(
            cursor, tenant_id, str(intent["action_class"]), datetime.now(UTC)
        )
        connector_view = grant
        consent = suppression = None
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
            release_activation=activation,
            release_activation_verified=bool(
                activation is not None
                and self._activation_verifier is not None
                and self._activation_verifier(activation)
            ),
            capability_inventory=inventory,
            capability_inventory_verified=bool(
                inventory is not None
                and self._inventory_verifier is not None
                and self._inventory_verifier(inventory)
            ),
            effect_draft_preview=preview,
            effect_policy=effect_policy,
            effect_context_loaded=True,
        )


class PlatformPolicyEvaluator:
    """Evaluate the exact current canonical policy bound to an effect class."""

    def evaluate(
        self,
        intent: dict[str, Any],
        state: HabitatState,
        evaluated_at: datetime,
    ) -> PolicyDisposition:
        policy = state.effect_policy
        if not isinstance(policy, dict):
            return PolicyDisposition("prohibited", "policy-unavailable", "unconfigured")
        if policy.get("tenantId") != intent.get("tenant_id"):
            return PolicyDisposition("prohibited", "policy-invalid", "tenant-mismatch")
        if policy.get("status") != "current":
            return PolicyDisposition("prohibited", "policy-invalid", "not-current")
        try:
            effective_from = _timestamp(str(policy["effectiveFrom"]))
            expires_at = _timestamp(str(policy["expiresAt"]))
        except (KeyError, TypeError, ValueError):
            return PolicyDisposition("prohibited", "policy-invalid", "malformed")
        now = evaluated_at.astimezone(UTC)
        if effective_from > now or expires_at <= now:
            return PolicyDisposition("prohibited", "policy-invalid", "outside-effective-window")
        rule = policy.get("selectedRule")
        if not isinstance(rule, dict) or rule.get("actionClass") != intent.get("action_class"):
            return PolicyDisposition("prohibited", "policy-invalid", "rule-mismatch")
        disposition = rule.get("disposition")
        if disposition not in {"allowed", "prohibited", "approval_required"}:
            return PolicyDisposition("prohibited", "policy-invalid", "invalid-disposition")
        return PolicyDisposition(
            disposition,
            str(policy.get("policyId") or policy.get("recordId") or "policy-invalid"),
            str(policy.get("policyVersion") or policy.get("recordVersion") or "unknown"),
        )


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
