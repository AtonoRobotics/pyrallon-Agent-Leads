"""Evidence and authority guards for under-contract coordination."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .structural import validate_record


class TransactionRuntimeError(ValueError):
    """Raised when a transaction plan would exceed confirmed operational authority."""


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise TransactionRuntimeError("transaction timestamps require an offset")
    return parsed.astimezone(UTC)


class TransactionRuntime:
    """Coordinate confirmed transaction tasks without interpreting contract language."""

    def validate_transaction(self, transaction: dict[str, Any]) -> None:
        validate_record(transaction, "ontology")
        if transaction.get("recordType") != "Transaction":
            raise TransactionRuntimeError("transaction_record_required")
        if transaction.get("transactionState") not in {
            "under_contract",
            "active",
            "closing_pending",
            "closed",
            "terminated",
            "cancelled",
            "disputed",
        }:
            raise TransactionRuntimeError("transaction_state_invalid")
        if not transaction.get("executedArtifactId") or not transaction.get(
            "executedArtifactDigest"
        ):
            raise TransactionRuntimeError("executed_contract_artifact_required")

    def validate_confirmed_date(
        self, transaction: dict[str, Any], date_record: dict[str, Any]
    ) -> None:
        self.validate_transaction(transaction)
        validate_record(date_record, "ontology")
        if date_record.get("recordType") != "ConfirmedTransactionDate":
            raise TransactionRuntimeError("confirmed_transaction_date_required")
        if date_record.get("transactionId") != transaction.get("id"):
            raise TransactionRuntimeError("transaction_date_binding_mismatch")
        if date_record.get("confirmationState") != "confirmed":
            raise TransactionRuntimeError("transaction_date_not_agent_confirmed")
        if date_record.get("confirmationSourceId") not in transaction.get(
            "sourceEvidenceIds", []
        ) and date_record.get("confirmationSourceId") != transaction.get("executedArtifactId"):
            raise TransactionRuntimeError("transaction_date_source_not_bound")
        if date_record.get("createdBy", {}).get("actorType") not in {
            "license_holder",
            "system_migration",
        }:
            raise TransactionRuntimeError("transaction_date_confirmation_actor_invalid")

    def build_plan(
        self,
        *,
        transaction: dict[str, Any],
        milestones: list[dict[str, Any]],
        confirmed_dates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Return an evidence-linked operational plan; it never interprets legal terms."""
        self.validate_transaction(transaction)
        confirmed_by_type: dict[str, dict[str, Any]] = {}
        for date_record in confirmed_dates:
            self.validate_confirmed_date(transaction, date_record)
            existing = confirmed_by_type.get(str(date_record["dateType"]))
            if existing is not None and existing["id"] != date_record["id"]:
                raise TransactionRuntimeError("transaction_date_conflict")
            confirmed_by_type[str(date_record["dateType"])] = date_record

        plan_milestones: list[dict[str, Any]] = []
        for milestone in milestones:
            validate_record(milestone, "ontology")
            if milestone.get("recordType") != "TransactionMilestone":
                raise TransactionRuntimeError("transaction_milestone_required")
            if milestone.get("transactionId") != transaction.get("id"):
                raise TransactionRuntimeError("transaction_milestone_binding_mismatch")
            if milestone.get("confirmationState") != "confirmed":
                raise TransactionRuntimeError("transaction_milestone_not_confirmed")
            if not milestone.get("confirmationEvidenceId"):
                raise TransactionRuntimeError(
                    "transaction_milestone_confirmation_evidence_required"
                )
            date_type = str(milestone.get("milestoneType"))
            milestone_date = confirmed_by_type.get(date_type)
            if milestone_date is None:
                raise TransactionRuntimeError("transaction_milestone_date_not_confirmed")
            if _timestamp(str(milestone["dueAt"])) != _timestamp(str(milestone_date["date"])):
                raise TransactionRuntimeError("transaction_milestone_date_conflict")
            plan_milestones.append(
                {
                    "milestoneId": milestone["id"],
                    "milestoneType": milestone["milestoneType"],
                    "dueAt": milestone["dueAt"],
                    "state": milestone["milestoneState"],
                    "confirmationEvidenceId": milestone["confirmationEvidenceId"],
                    "sourceArtifactId": transaction["executedArtifactId"],
                }
            )
        result = {
            "messageType": "transaction_coordination_plan",
            "schemaVersion": "transaction-coordination/1.0.0",
            "transactionId": transaction["id"],
            "journeyId": transaction["journeyId"],
            "executedArtifactId": transaction["executedArtifactId"],
            "executedArtifactDigest": transaction["executedArtifactDigest"],
            "transactionState": transaction["transactionState"],
            "milestones": sorted(
                plan_milestones, key=lambda item: (item["dueAt"], item["milestoneId"])
            ),
            "unresolvedItems": [],
            "legalInterpretation": False,
        }
        validate_record(result, "transaction_coordination")
        return result

    def authorize_deadline_action(
        self,
        *,
        plan: dict[str, Any],
        milestone_id: str,
        action_evidence_id: str,
        actor_type: str,
    ) -> dict[str, Any]:
        if (
            plan.get("messageType") != "transaction_coordination_plan"
            or plan.get("legalInterpretation") is not False
        ):
            raise TransactionRuntimeError("transaction_plan_invalid")
        if actor_type not in {"license_holder", "service_principal"}:
            raise TransactionRuntimeError("deadline_action_actor_invalid")
        if not action_evidence_id:
            raise TransactionRuntimeError("deadline_action_evidence_required")
        milestone = next(
            (
                item
                for item in plan.get("milestones", [])
                if item.get("milestoneId") == milestone_id
            ),
            None,
        )
        if milestone is None:
            raise TransactionRuntimeError("deadline_milestone_not_in_plan")
        if milestone.get("state") not in {"pending", "due"}:
            raise TransactionRuntimeError("deadline_milestone_not_actionable")
        return {
            "transactionId": plan["transactionId"],
            "milestoneId": milestone_id,
            "dueAt": milestone["dueAt"],
            "executedArtifactId": plan["executedArtifactId"],
            "confirmationEvidenceId": milestone["confirmationEvidenceId"],
            "actionEvidenceId": action_evidence_id,
            "actorType": actor_type,
            "legalInterpretation": False,
        }
