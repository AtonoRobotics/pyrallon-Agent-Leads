"""Fail closed if repository execution has been narrowed below production scope."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "PRODUCTION-COMPLETION-LEDGER.yaml"
MANDATE_PATH = ROOT / "PRODUCTION-EXECUTION-MANDATE.yaml"
REQUIRED_SCOPE_MODE = "whole_repository_production"
REQUIRED_WORKSTREAMS = {
    "domain_and_workflows",
    "provider_ingress_and_senders",
    "cognitive_production_routing",
    "nurture_and_transaction_operations",
    "calendar_and_esignature",
    "connectors_and_webhooks",
    "operator_api_and_web_ui",
    "ios_client_and_offline_contract",
    "postgres_backup_and_restore",
    "temporal_replay_and_recovery",
    "security_observability_and_evaluations",
    "deployment_and_release_operations",
}
FORBIDDEN_EXECUTION_MODES = {
    "mvp",
    "slice",
    "partial",
    "prototype",
    "demo",
    "toy",
    "vertical_slice",
    "narrowed_scope",
}


def validate_production_scope(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    scope = document.get("scope")
    if not isinstance(scope, dict):
        return ["scope is required; whole-repository production scope cannot be omitted"]
    if scope.get("mode") != REQUIRED_SCOPE_MODE:
        errors.append(f"scope.mode must be {REQUIRED_SCOPE_MODE}")

    required_policy = {
        "completion_target": "whole_repository",
        "allow_scope_reduction": False,
        "allow_partial_release": False,
        "allow_mvp_or_slice_mode": False,
        "required_workstream_set": "exact",
    }
    if scope.get("execution_policy") != required_policy:
        errors.append(
            "scope.execution_policy must require whole_repository, exact workstreams, "
            "and forbid scope reduction, partial release, MVP, and slice mode"
        )

    workstreams = scope.get("workstreams")
    ids = {
        item.get("id")
        for item in workstreams or []
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if ids != REQUIRED_WORKSTREAMS:
        errors.append("scope.workstreams must equal the exact required production workstream set")
    return errors


def validate_execution_mandate(document: dict[str, Any]) -> list[str]:
    """Validate the non-reducible execution contract used before implementation."""
    errors: list[str] = []
    if document.get("version") != 1:
        errors.append("execution mandate version must be 1")
    if document.get("mode") != REQUIRED_SCOPE_MODE:
        errors.append(f"execution mandate mode must be {REQUIRED_SCOPE_MODE}")
    if document.get("objective") != "complete_and_release_the_entire_repository_in_production":
        errors.append(
            "execution mandate objective must require entire-repository production completion"
        )

    workstreams = document.get("required_workstreams")
    ids = {
        item.get("id")
        for item in workstreams or []
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if ids != REQUIRED_WORKSTREAMS:
        errors.append("execution mandate must contain the exact required production workstream set")
    if isinstance(workstreams, list) and [
        item.get("id") for item in workstreams if isinstance(item, dict)
    ] != sorted(REQUIRED_WORKSTREAMS):
        errors.append("execution mandate workstreams must use the canonical sorted order")

    prohibited = document.get("prohibited_modes")
    if not isinstance(prohibited, list) or not FORBIDDEN_EXECUTION_MODES.issubset(set(prohibited)):
        errors.append(
            "execution mandate must prohibit MVP, slice, partial, prototype, demo, and toy modes"
        )
    if (
        document.get("completion_rule")
        != "all_required_workstreams_and_all_ledger_items_and_all_release_predicates"
    ):
        errors.append(
            "execution mandate completion rule must require every workstream, ledger item, and release predicate"
        )
    if document.get("allow_workstream_removal") is not False:
        errors.append("execution mandate must forbid workstream removal")
    if document.get("allow_scope_reduction") is not False:
        errors.append("execution mandate must forbid scope reduction")
    return errors


def main() -> int:
    try:
        document = yaml.safe_load(LEDGER_PATH.read_text())
        errors = validate_production_scope(document if isinstance(document, dict) else {})
        mandate = yaml.safe_load(MANDATE_PATH.read_text())
        errors.extend(validate_execution_mandate(mandate if isinstance(mandate, dict) else {}))
    except (OSError, yaml.YAMLError) as exc:
        print(f"production scope or execution mandate validation failed: {exc}", file=sys.stderr)
        return 1
    if errors:
        print("production scope validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("whole-repository production scope is mechanically enforced")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
