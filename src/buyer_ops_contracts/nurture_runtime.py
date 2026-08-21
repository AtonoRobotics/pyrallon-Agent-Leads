"""Policy-bounded contextual nurture planning with consent and commitment stops."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .contract_acceptance import canonical_digest
from .structural import validate_record


class NurtureRuntimeError(ValueError):
    """Raised when a nurture plan would violate policy, consent, or authority."""


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise NurtureRuntimeError("nurture timestamps require an offset")
    return parsed.astimezone(UTC)


class NurtureRuntime:
    def __init__(self, *, deriver_id: str, implementation_version: str) -> None:
        if not deriver_id or not implementation_version:
            raise ValueError("nurture deriver identity is required")
        self._deriver_id = deriver_id
        self._implementation_version = implementation_version

    def plan(
        self,
        *,
        policy: dict[str, Any],
        journey: dict[str, Any],
        consent_state: str,
        contactability_state: str,
        representation_state: str,
        now: datetime,
        last_interaction_at: str | None,
        unresolved_commitments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        validate_record(policy, "nurture_plan")
        validate_record(journey, "ontology")
        if policy.get("messageType") != "nurture_policy" or policy.get("lifecycle") != "active":
            raise NurtureRuntimeError("nurture_policy_not_active")
        if policy.get("tenantId") != journey.get("tenantId"):
            raise NurtureRuntimeError("nurture_policy_tenant_mismatch")
        now = now.astimezone(UTC)
        stop_reasons: list[str] = []
        eligible_states = {
            "captured",
            "contacted",
            "qualifying",
            "nurture",
            "consultation_ready",
            "dormant",
        }
        if consent_state in {"opted_out", "revoked", "unknown"}:
            stop_reasons.append(f"consent_{consent_state}")
        if contactability_state not in {"contactable"}:
            stop_reasons.append(f"contactability_{contactability_state}")
        if representation_state in {"conflict", "terminated", "expired"}:
            stop_reasons.append(f"representation_{representation_state}")
        if journey.get("journeyState") not in eligible_states:
            stop_reasons.append("journey_not_nurture_eligible")

        unresolved = []
        overdue = []
        for commitment in unresolved_commitments:
            validate_record(commitment, "ontology")
            if commitment.get("recordType") != "Commitment" or commitment.get(
                "commitmentState"
            ) not in {"open", "in_progress", "blocked"}:
                continue
            unresolved.append(str(commitment["id"]))
            if _time(str(commitment["dueAt"])) <= now:
                overdue.append(str(commitment["id"]))
        stalled = bool(last_interaction_at) and now - _time(str(last_interaction_at)) >= timedelta(
            seconds=int(policy["stalledAfterSeconds"])
        )
        evidence_ids = sorted(set(policy["sourceEvidenceIds"]) | set(overdue))
        if not evidence_ids:
            evidence_ids = list(policy["sourceEvidenceIds"])

        action: dict[str, Any] | None = None
        plan_state = "active"
        if stop_reasons:
            plan_state = (
                "paused"
                if any(reason.startswith("consent_") for reason in stop_reasons)
                else "blocked"
            )
        elif overdue:
            action = {
                "actionId": f"nurture-action:{journey['id']}:commitment",
                "actionType": "resolve_promised_followup",
                "channel": "agent_task",
                "dueAt": now.isoformat().replace("+00:00", "Z"),
                "reasonCodes": ["unresolved_promised_followup"],
                "evidenceIds": evidence_ids,
            }
        elif stalled:
            action_type = next(
                (
                    item
                    for item in policy["approvedActionTypes"]
                    if item in {"useful_next_step", "answer_open_question", "offer_consultation"}
                ),
                None,
            )
            if action_type is None:
                plan_state = "blocked"
                stop_reasons.append("no_approved_stalled_conversation_action")
            else:
                action = {
                    "actionId": f"nurture-action:{journey['id']}:stalled",
                    "actionType": action_type,
                    "channel": policy["channels"][0],
                    "dueAt": now.isoformat().replace("+00:00", "Z"),
                    "reasonCodes": ["stalled_conversation", "useful_next_step_required"],
                    "evidenceIds": evidence_ids,
                }
        else:
            action_type = next(iter(policy["approvedActionTypes"]), None)
            if action_type is None:
                plan_state = "blocked"
                stop_reasons.append("no_approved_nurture_action")
            else:
                due = now + timedelta(seconds=int(policy["maxFrequencySeconds"]))
                action = {
                    "actionId": f"nurture-action:{journey['id']}:next",
                    "actionType": action_type,
                    "channel": policy["channels"][0],
                    "dueAt": due.isoformat().replace("+00:00", "Z"),
                    "reasonCodes": ["contextual_followup"],
                    "evidenceIds": evidence_ids,
                }
        payload = {
            "tenant_id": journey["tenantId"],
            "journey_id": journey["id"],
            "policy_id": policy["policyId"],
            "policy_version": policy["version"],
            "consent_state": consent_state,
            "contactability_state": contactability_state,
            "representation_state": representation_state,
            "last_interaction_at": last_interaction_at,
            "unresolved_commitment_ids": sorted(unresolved),
            "stalled": stalled,
        }
        result = {
            "messageType": "nurture_plan",
            "schemaVersion": "nurture-plan/1.0.0",
            "tenantId": journey["tenantId"],
            "journeyId": journey["id"],
            "planId": f"nurture-plan:{journey['id']}:{policy['version']}",
            "policyId": policy["policyId"],
            "policyVersion": policy["version"],
            "planState": plan_state,
            "stalled": stalled,
            "unresolvedCommitmentIds": sorted(unresolved),
            "stopReasons": sorted(set(stop_reasons)),
            "nextAction": action,
            "derivedAt": now.isoformat().replace("+00:00", "Z"),
            "inputDigest": canonical_digest(payload),
            "evidenceIds": evidence_ids,
        }
        validate_record(result, "nurture_plan")
        return result
