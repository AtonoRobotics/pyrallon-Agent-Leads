"""Semantic admission for operator-surface/1.1.0 records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .digest import sha256_digest
from .errors import ContractViolation, Violation
from .semantic import validate_semantics
from .structural import validate_record

OPERATOR_COMMAND_TARGETS: dict[str, frozenset[str]] = {
    "approve": frozenset({"Approval"}),
    "deny": frozenset({"Approval"}),
    "correct_replace": frozenset({"Assertion", "VerifiedFact", "Inference", "Memory"}),
    "correct_invalidate": frozenset({"Assertion", "VerifiedFact", "Inference", "Memory"}),
    "revoke_authorization": frozenset({"Authorization"}),
    "revoke_approval": frozenset({"Approval"}),
    "pause_workflow": frozenset({"WorkflowReference"}),
    "resume_workflow": frozenset({"WorkflowReference"}),
    "request_reconciliation": frozenset({"EffectAttempt"}),
}


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include an offset")
    return parsed.astimezone(UTC)


def operator_payload_digest(command: dict[str, Any]) -> str:
    """Digest the complete governed mutation intent, excluding envelope metadata."""
    return sha256_digest(
        {
            "command_type": command["command_type"],
            "target_record_id": command["target_record_id"],
            "expected_version": command["expected_version"],
            "reason": command["reason"],
            "mutation": command.get("mutation"),
        }
    )


def validate_operator_semantics(record: dict[str, Any]) -> None:
    """Apply cross-field rules not expressible in the Operator Surface schema."""
    violations: list[Violation] = []
    message_type = record.get("message_type")
    if message_type == "operator_policy":
        rules = [str(rule["command_type"]) for rule in record["command_rules"]]
        if len(rules) != len(set(rules)):
            violations.append(
                Violation(
                    "DUPLICATE_OPERATOR_COMMAND_RULE",
                    "$.command_rules",
                    "a command type has more than one authority mapping",
                )
            )
        if record["status"] == "active" and "effective_to" in record:
            violations.append(
                Violation(
                    "ACTIVE_OPERATOR_POLICY_CLOSED",
                    "$.effective_to",
                    "an active policy cannot have a closed effective interval",
                )
            )
        if record["status"] in {"superseded", "retired"} and "effective_to" not in record:
            violations.append(
                Violation(
                    "CLOSED_OPERATOR_POLICY_END_REQUIRED",
                    "$.effective_to",
                    "a non-current policy requires an effective interval end",
                )
            )
        if int(record["record_version"]) > 1 and not record.get("supersedes_policy_id"):
            violations.append(
                Violation(
                    "OPERATOR_POLICY_SUPERSESSION_REQUIRED",
                    "$.supersedes_policy_id",
                    "versions after one must identify the superseded policy",
                )
            )
        if "effective_to" in record and _time(record["effective_to"]) <= _time(
            record["effective_from"]
        ):
            violations.append(
                Violation(
                    "OPERATOR_POLICY_TEMPORAL_ORDER",
                    "$.effective_to",
                    "must follow effective_from",
                )
            )
    elif message_type == "operator_command":
        authority = record["authority"]
        if (
            authority["resource_id"] != record["target_record_id"]
            or authority["resource_type"] != record["target_record_type"]
        ):
            violations.append(
                Violation(
                    "OPERATOR_AUTHORITY_RESOURCE_MISMATCH",
                    "$.authority.resource_id",
                    "authority resource must equal the exact command target",
                )
            )
        if authority["policy_ref"]["record_type"] != "OperatorPolicy":
            violations.append(
                Violation(
                    "OPERATOR_POLICY_REF_TYPE",
                    "$.authority.policy_ref.record_type",
                    "policy_ref must identify an OperatorPolicy",
                )
            )
        if any(
            item["record_type"] != "ActorTenantAuthorization"
            or item["status"] != "active"
            for item in authority["authorization_refs"]
        ):
            violations.append(
                Violation(
                    "OPERATOR_AUTHORIZATION_REF_STATE",
                    "$.authority.authorization_refs",
                    "every authority reference must identify an active ActorTenantAuthorization",
                )
            )
        if operator_payload_digest(record) != record["payload_digest"]:
            violations.append(
                Violation(
                    "OPERATOR_PAYLOAD_DIGEST_MISMATCH",
                    "$.payload_digest",
                    "does not bind the complete canonical mutation intent",
                )
            )
        if _time(record["issued_at"]) >= _time(record["expires_at"]):
            violations.append(
                Violation(
                    "OPERATOR_COMMAND_TEMPORAL_ORDER",
                    "$.expires_at",
                    "must follow issued_at",
                )
            )
        mutation = record.get("mutation")
        if mutation is not None:
            for field in (
                "correction_record",
                "corrected_item_update",
                "replacement_record",
                "authorization_update",
                "prior_approval_update",
                "revoked_approval_record",
            ):
                nested = mutation.get(field)
                if nested is None:
                    continue
                validate_record(nested, "ontology")
                validate_semantics(nested)
                if nested["tenantId"] != record["tenant_id"]:
                    violations.append(
                        Violation(
                            "OPERATOR_MUTATION_TENANT_MISMATCH",
                            f"$.mutation.{field}.tenantId",
                            "canonical mutation must remain in the command tenant",
                        )
                    )
            kind = mutation["kind"]
            if kind == "correction":
                correction = mutation["correction_record"]
                corrected = mutation["corrected_item_update"]
                replacement = mutation.get("replacement_record")
                expected_action = (
                    "replace" if record["command_type"] == "correct_replace" else "invalidate"
                )
                if (
                    correction["recordType"] != "Correction"
                    or correction["correctionState"] != "applied"
                    or correction["correctionAction"] != expected_action
                    or correction["correctedItemId"] != record["target_record_id"]
                    or corrected["id"] != record["target_record_id"]
                    or corrected["recordType"] != record["target_record_type"]
                    or corrected["version"] != record["expected_version"] + 1
                ):
                    violations.append(
                        Violation(
                            "OPERATOR_CORRECTION_BINDING",
                            "$.mutation",
                            "correction records must bind the exact command target, action, and next version",
                        )
                    )
                if expected_action == "replace":
                    if replacement is None or correction.get(
                        "replacementItemId"
                    ) != replacement.get("id"):
                        violations.append(
                            Violation(
                                "OPERATOR_CORRECTION_REPLACEMENT",
                                "$.mutation.replacement_record",
                                "replace requires the exact declared replacement record",
                            )
                        )
                elif replacement is not None:
                    violations.append(
                        Violation(
                            "OPERATOR_CORRECTION_REPLACEMENT",
                            "$.mutation.replacement_record",
                            "invalidate forbids a replacement record",
                        )
                    )
            elif kind == "authorization_revocation":
                update = mutation["authorization_update"]
                if (
                    update["recordType"] != "Authorization"
                    or update["id"] != record["target_record_id"]
                    or update["version"] != record["expected_version"] + 1
                    or update["authorizationState"] != "revoked"
                    or not update.get("revokedAt")
                    or not update.get("revocationEvidenceId")
                ):
                    violations.append(
                        Violation(
                            "OPERATOR_AUTHORIZATION_REVOCATION_BINDING",
                            "$.mutation.authorization_update",
                            "revocation must be the exact evidenced next Authorization version",
                        )
                    )
            elif kind == "approval_revocation":
                prior = mutation["prior_approval_update"]
                revoked = mutation["revoked_approval_record"]
                if (
                    prior["recordType"] != "Approval"
                    or prior["id"] != record["target_record_id"]
                    or prior["version"] != record["expected_version"] + 1
                    or prior["status"] != "superseded"
                    or revoked["recordType"] != "Approval"
                    or revoked["version"] != 1
                    or revoked["decision"] != "revoked"
                    or revoked.get("supersedesId") != record["target_record_id"]
                    or prior.get("effectiveTo") != revoked.get("effectiveFrom")
                ):
                    violations.append(
                        Violation(
                            "OPERATOR_APPROVAL_REVOCATION_BINDING",
                            "$.mutation",
                            "approval revocation must atomically close the target and add its exact revoked successor",
                        )
                    )
    if violations:
        raise ContractViolation(violations)
