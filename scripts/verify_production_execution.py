"""Fail closed when production execution drifts into a narrowed workstream."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "PRODUCTION-EXECUTION-STATE.yaml"
LEDGER_PATH = ROOT / "PRODUCTION-COMPLETION-LEDGER.yaml"
MANDATE_PATH = ROOT / "PRODUCTION-EXECUTION-MANDATE.yaml"

CANONICAL_SEQUENCE = [
    "calendar_and_esignature",
    "cognitive_production_routing",
    "connectors_and_webhooks",
    "deployment_and_release_operations",
    "domain_and_workflows",
    "ios_client_and_offline_contract",
    "nurture_and_transaction_operations",
    "operator_api_and_web_ui",
    "postgres_backup_and_restore",
    "provider_ingress_and_senders",
    "security_observability_and_evaluations",
    "temporal_replay_and_recovery",
]
REQUIRED_OBJECTIVE = "complete_and_release_the_entire_repository_in_production"
REQUIRED_PROHIBITED_MODES = {
    "mvp",
    "slice",
    "partial",
    "prototype",
    "demo",
    "toy",
    "vertical_slice",
    "narrowed_scope",
}
REQUIRED_COMPLETION_RULE = (
    "all_required_workstreams_and_all_ledger_items_and_all_release_predicates"
)
GENERIC_EXECUTION_TEXT = {
    "implement and provider-test the current workstream completely",
    "provider-backed end-to-end evidence plus automated verification",
}


def validate_execution_state(
    state: dict[str, Any], ledger: dict[str, Any], mandate: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    if state.get("version") != 1:
        errors.append("execution state version must be 1")
    if state.get("objective") != REQUIRED_OBJECTIVE:
        errors.append(
            "execution state objective must require entire-repository production completion"
        )
    if state.get("scope_mode") != "whole_repository_production":
        errors.append("execution state scope_mode must be whole_repository_production")
    if mandate.get("objective") != REQUIRED_OBJECTIVE:
        errors.append("execution mandate objective does not match execution state")
    if set(mandate.get("prohibited_modes", [])) != REQUIRED_PROHIBITED_MODES:
        errors.append("execution mandate prohibited_modes cannot be weakened")
    if mandate.get("allow_workstream_removal") is not False:
        errors.append("execution mandate must prohibit workstream removal")
    if mandate.get("allow_scope_reduction") is not False:
        errors.append("execution mandate must prohibit scope reduction")
    if mandate.get("completion_rule") != REQUIRED_COMPLETION_RULE:
        errors.append("execution mandate completion_rule must require every release predicate")

    required_workstreams = mandate.get("required_workstreams")
    if not isinstance(required_workstreams, list) or any(
        not isinstance(item, dict)
        or item.get("completion") != "implementation_provider_e2e_evidence"
        for item in required_workstreams
    ):
        errors.append(
            "every required workstream must require implementation, provider E2E, and evidence"
        )

    sequence = state.get("canonical_sequence")
    if sequence != CANONICAL_SEQUENCE:
        errors.append("execution state canonical_sequence must equal the fixed production sequence")
    mandate_ids = [
        item.get("id") for item in mandate.get("required_workstreams", []) if isinstance(item, dict)
    ]
    if mandate_ids != CANONICAL_SEQUENCE:
        errors.append(
            "execution mandate workstream order does not match the fixed production sequence"
        )

    completed = state.get("completed_workstreams")
    remaining = state.get("remaining_workstreams")
    if not isinstance(completed, list) or not isinstance(remaining, list):
        return errors + ["completed_workstreams and remaining_workstreams must be lists"]
    if len(set(completed)) != len(completed) or len(set(remaining)) != len(remaining):
        errors.append("execution cursor workstream lists must not contain duplicates")
    if completed + remaining != CANONICAL_SEQUENCE:
        errors.append(
            "execution cursor must partition the exact canonical sequence without skipping"
        )

    current = state.get("current_workstream")
    expected_current = remaining[0] if remaining else None
    if current != expected_current:
        errors.append("current_workstream must be the first remaining canonical workstream")
    next_action = state.get("next_action")
    if not isinstance(next_action, str) or not next_action.strip():
        errors.append("next_action must state the concrete production action")
    elif next_action.strip().lower() in GENERIC_EXECUTION_TEXT:
        errors.append("next_action must be concrete; generic workstream placeholders are forbidden")
    elif current and current not in next_action:
        errors.append("next_action must name the current workstream")

    required_proof = state.get("required_proof")
    if not isinstance(required_proof, str) or not required_proof.strip():
        errors.append("required_proof must state the required production evidence")
    elif required_proof.strip().lower() in GENERIC_EXECUTION_TEXT:
        errors.append("required_proof must specify concrete provider and automated evidence")
    else:
        proof = required_proof.lower()
        for term in ("provider", "end-to-end", "automated"):
            if term not in proof:
                errors.append(f"required_proof must include {term} evidence")

    scope_workstreams = ledger.get("scope", {}).get("workstreams", [])
    ledger_by_id = {
        item.get("id"): item
        for item in scope_workstreams
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for workstream_id in completed:
        if ledger_by_id.get(workstream_id, {}).get("status") != "complete":
            errors.append(f"completed cursor workstream is not complete in ledger: {workstream_id}")
        workstream = next(
            (
                item
                for item in scope_workstreams
                if isinstance(item, dict) and item.get("id") == workstream_id
            ),
            {},
        )
        provider_evidence = workstream.get("provider_e2e_evidence")
        if (
            not isinstance(provider_evidence, list)
            or not provider_evidence
            or not all(isinstance(path, str) and path.strip() for path in provider_evidence)
        ):
            errors.append(
                f"completed cursor workstream must declare provider_e2e_evidence: {workstream_id}"
            )
    if any(
        ledger_by_id.get(workstream_id, {}).get("status") == "complete"
        for workstream_id in remaining
    ):
        errors.append("remaining cursor contains a workstream already marked complete in ledger")
    return errors


def main() -> int:
    try:
        state = yaml.safe_load(STATE_PATH.read_text())
        ledger = yaml.safe_load(LEDGER_PATH.read_text())
        mandate = yaml.safe_load(MANDATE_PATH.read_text())
    except (OSError, yaml.YAMLError) as exc:
        print(f"production execution validation failed: {exc}", file=sys.stderr)
        return 1
    errors = validate_execution_state(
        state if isinstance(state, dict) else {},
        ledger if isinstance(ledger, dict) else {},
        mandate if isinstance(mandate, dict) else {},
    )
    if errors:
        for error in errors:
            print(f"production execution validation failed: {error}", file=sys.stderr)
        return 1
    print("whole-repository production execution cursor is mechanically enforced")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
