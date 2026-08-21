"""Provider-neutral, evidence-bound progressive qualification runtime."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .contract_acceptance import canonical_digest
from .structural import validate_record


class QualificationRuntimeError(ValueError):
    """Raised when qualification cannot be evaluated under its published policy."""


_OBSERVATION_STATES = {
    "unknown",
    "asserted",
    "verified",
    "inferred",
    "buyer_declined",
    "stale",
    "contradicted",
    "not_applicable",
}


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise QualificationRuntimeError("qualification timestamps require an offset")
    return parsed.astimezone(UTC)


def _ref(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "recordId": record["id"],
        "recordType": record["recordType"],
        "version": record["version"],
    }


def _observation_for(
    criterion_id: str,
    observations: list[dict[str, Any]],
    evaluated_at: datetime,
    max_age_seconds: int,
) -> dict[str, Any] | None:
    candidates = [
        item
        for item in observations
        if item.get("criterionId") == criterion_id
        and item.get("recordType") == "QualificationObservation"
        and item.get("status") == "active"
    ]
    candidates.sort(key=lambda item: (_time(str(item["updatedAt"])), str(item["id"])), reverse=True)
    for item in candidates:
        observed_at = _time(str(item["updatedAt"]))
        state = item.get("observationState")
        if state not in _OBSERVATION_STATES:
            raise QualificationRuntimeError("qualification observation state is unsupported")
        return {
            "criterionId": criterion_id,
            "observationRef": _ref(item),
            "observationState": state,
            "observedAt": observed_at.isoformat().replace("+00:00", "Z"),
            "validAtEvaluation": (
                evaluated_at - observed_at <= timedelta(seconds=max_age_seconds)
                and observed_at <= evaluated_at
                and state not in {"stale", "contradicted"}
            ),
            "contradictionRefs": [],
        }
    return None


class QualificationRuntime:
    """Evaluate a published qualification policy without model or provider authority."""

    def __init__(self, *, deriver_principal_id: str, implementation_version: str) -> None:
        if not deriver_principal_id or not implementation_version:
            raise ValueError("qualification deriver identity is required")
        self._deriver = {
            "principalId": deriver_principal_id,
            "implementationId": "qualification_readiness_v1",
            "implementationVersion": implementation_version,
        }

    def evaluate(
        self,
        *,
        policy: dict[str, Any],
        journey: dict[str, Any],
        observations: list[dict[str, Any]],
        evaluated_at: datetime,
        service_zone_decision_ref: dict[str, Any],
        service_zone_eligible: bool,
        capacity_decision_ref: dict[str, Any],
        capacity_available: bool,
        urgent_escalation_refs: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Return the governed input set, next-question decision, and readiness decision."""
        validate_record(policy, "qualification_readiness")
        if policy.get("messageType") != "qualification_policy":
            raise QualificationRuntimeError("qualification policy message type is required")
        if policy.get("lifecycle") != "active":
            raise QualificationRuntimeError("qualification policy is not active")
        validate_record(journey, "ontology")
        if journey.get("recordType") != "BuyerJourney":
            raise QualificationRuntimeError("qualification journey must be BuyerJourney")
        if any(item.get("tenantId") != journey.get("tenantId") for item in observations):
            raise QualificationRuntimeError("qualification observations cross tenant boundary")
        evaluated_at = evaluated_at.astimezone(UTC)
        evaluated_text = evaluated_at.isoformat().replace("+00:00", "Z")
        policy_ref = {
            "recordId": policy["policyId"],
            "recordType": "QualificationPolicy",
            "version": policy["version"],
        }
        journey_ref = _ref(journey)
        bindings: list[dict[str, Any]] = []
        for rule in policy["criteria"]:
            binding = _observation_for(
                str(rule["criterionId"]), observations, evaluated_at, int(rule["maxAgeSeconds"])
            )
            if binding is None:
                binding = {
                    "criterionId": rule["criterionId"],
                    "observationRef": {
                        "recordId": f"unknown:{journey['id']}:{rule['criterionId']}",
                        "recordType": "QualificationObservation",
                        "version": 1,
                    },
                    "observationState": "unknown",
                    "observedAt": evaluated_text,
                    "validAtEvaluation": False,
                    "contradictionRefs": [],
                }
            bindings.append(binding)

        input_payload = {
            "tenantId": journey["tenantId"],
            "journeyRef": journey_ref,
            "policyRef": policy_ref,
            "evaluatedAt": evaluated_text,
            "observations": bindings,
            "serviceZoneDecisionRef": service_zone_decision_ref,
            "serviceZoneEligible": service_zone_eligible,
            "capacityDecisionRef": capacity_decision_ref,
            "capacityAvailable": capacity_available,
            "urgentEscalationRefs": urgent_escalation_refs,
            "canonicalWatermark": f"{journey['id']}:{journey['version']}",
        }
        input_set = {
            "messageType": "qualification_input_set",
            "schemaVersion": "qualification-readiness/1.0.0",
            "tenantId": journey["tenantId"],
            "inputSetId": f"qualification-input:{journey['id']}:{journey['version']}",
            **input_payload,
            "inputDigest": canonical_digest(input_payload),
        }
        validate_record(input_set, "qualification_readiness")

        by_criterion = {item["criterionId"]: item for item in bindings}
        next_rule: dict[str, Any] | None = None
        next_reason: list[str] = []
        blocking_ids: list[str] = []
        unresolved_required = False
        for rule in sorted(
            policy["criteria"], key=lambda item: (item["priority"], item["criterionId"])
        ):
            binding = by_criterion[rule["criterionId"]]
            state = binding["observationState"]
            resolved = binding["validAtEvaluation"] and state in set(
                rule["acceptedObservationStates"]
            )
            if rule["disposition"] == "required" and not resolved:
                unresolved_required = True
            if state == "contradicted":
                blocking_ids.append(rule["criterionId"])
                if rule["contradictionDisposition"] == "block_readiness":
                    next_reason.append("contradiction_blocks_readiness")
                elif next_rule is None:
                    next_rule = rule
                    next_reason.append("contradiction_requires_clarification")
            elif not resolved and state not in {"buyer_declined", "not_applicable"}:
                if next_rule is None:
                    next_rule = rule
                    next_reason.append(
                        "stale_observation" if state == "stale" else "missing_observation"
                    )

        if not service_zone_eligible:
            next_reason.append("service_zone_ineligible")
        if not capacity_available:
            next_reason.append("agent_capacity_unavailable")
        if blocking_ids or not service_zone_eligible or not capacity_available:
            readiness_result = "blocked"
        elif unresolved_required:
            readiness_result = "not_ready"
        else:
            readiness_result = "ready"

        if next_rule is None:
            next_result = "blocked" if readiness_result == "blocked" else "no_question"
            next_criterion: str | None = None
            next_template: dict[str, Any] | None = None
        elif next_rule["missingDisposition"] == "agent_handle":
            next_result = "agent_handle"
            next_criterion = next_rule["criterionId"]
            next_template = next_rule["questionTemplateRef"]
        elif next_rule["missingDisposition"] == "block_readiness":
            next_result = "blocked"
            next_criterion = next_rule["criterionId"]
            next_template = next_rule["questionTemplateRef"]
        else:
            next_result = "ask"
            next_criterion = next_rule["criterionId"]
            next_template = next_rule["questionTemplateRef"]

        next_question = {
            "messageType": "next_question_decision",
            "schemaVersion": "qualification-readiness/1.0.0",
            "tenantId": journey["tenantId"],
            "decisionId": f"next-question:{journey['id']}:{journey['version']}",
            "inputSetRef": _ref_message(input_set, "inputSetId", "QualificationInputSet"),
            "policyRef": policy_ref,
            "result": next_result,
            "criterionId": next_criterion,
            "questionTemplateRef": next_template,
            "reasonCodes": sorted(set(next_reason or ["all_criteria_resolved"])),
            "derivedAt": evaluated_text,
            "derivedBy": self._deriver,
            "inputDigest": input_set["inputDigest"],
        }
        validate_record(next_question, "qualification_readiness")

        max_age = max(int(rule["maxAgeSeconds"]) for rule in policy["criteria"])
        readiness = {
            "messageType": "readiness_decision",
            "schemaVersion": "qualification-readiness/1.0.0",
            "tenantId": journey["tenantId"],
            "decisionId": f"readiness:{journey['id']}:{journey['version']}",
            "journeyRef": journey_ref,
            "inputSetRef": _ref_message(input_set, "inputSetId", "QualificationInputSet"),
            "policyRef": policy_ref,
            "result": readiness_result,
            "reasonCodes": sorted(set(next_reason or ["all_required_criteria_resolved"])),
            "blockingCriterionIds": sorted(set(blocking_ids)),
            "derivedAt": evaluated_text,
            "derivedBy": self._deriver,
            "inputDigest": input_set["inputDigest"],
            "expiresAt": (evaluated_at + timedelta(seconds=max_age))
            .isoformat()
            .replace("+00:00", "Z"),
            "evidenceIds": sorted(
                set(policy.get("sourceEvidenceIds", []))
                | {
                    str(item["observationRef"]["recordId"])
                    for item in bindings
                    if item["validAtEvaluation"]
                }
            ),
        }
        validate_record(readiness, "qualification_readiness")
        return {"input_set": input_set, "next_question": next_question, "readiness": readiness}


def _ref_message(record: dict[str, Any], id_key: str, record_type: str) -> dict[str, Any]:
    return {"recordId": record[id_key], "recordType": record_type, "version": 1}
