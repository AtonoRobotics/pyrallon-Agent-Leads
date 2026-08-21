"""Mechanically enforce the repository-wide production completion ledger."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "PRODUCTION-COMPLETION-LEDGER.yaml"
GROUPS = ("requirements", "packets", "surfaces")
STATUSES = {"complete", "incomplete", "not_started"}
REQUIRED_SCOPE_MODE = "whole_repository_production"
REQUIRED_EXECUTION_POLICY = {
    "completion_target": "whole_repository",
    "allow_scope_reduction": False,
    "allow_partial_release": False,
    "allow_mvp_or_slice_mode": False,
    "required_workstream_set": "exact",
}
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


def _path_exists(root: Path, value: str) -> bool:
    path = root / value
    return path.is_file() or path.is_dir()


def validate_production_ledger(document: dict[str, Any], root: Path) -> list[str]:
    """Validate ledger shape and reject any unsupported completion claim."""
    errors: list[str] = []
    if document.get("version") != 1:
        errors.append("version must be 1")
    if document.get("project") != "buyer-operations":
        errors.append("project must be buyer-operations")
    specification = document.get("specification")
    if not isinstance(specification, str) or not _path_exists(root, specification):
        errors.append(f"specification does not exist: {specification}")
    else:
        specification_text = (root / specification).read_text()
        required_specification_requirements = {
            f"FR-{int(match):02d}"
            for match in re.findall(r"^### FR-(\d+)\b", specification_text, re.MULTILINE)
        }
        declared_requirement_ids = {
            str(item.get("id"))
            for item in document.get("requirements", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        missing_specification_requirements = sorted(
            required_specification_requirements - declared_requirement_ids
        )
        if missing_specification_requirements:
            errors.append(
                "ledger omits functional requirements from the authoritative PRD: "
                + ", ".join(missing_specification_requirements)
            )
    if not isinstance(document.get("release_complete"), bool):
        errors.append("release_complete must be boolean")

    scope = document.get("scope")
    if not isinstance(scope, dict):
        errors.append("scope must be a mapping; whole-repository scope is mandatory")
        scope = {}
    if scope.get("mode") != REQUIRED_SCOPE_MODE:
        errors.append(f"scope.mode must be {REQUIRED_SCOPE_MODE}")
    if scope.get("execution_policy") != REQUIRED_EXECUTION_POLICY:
        errors.append(
            "scope.execution_policy must require whole_repository, exact workstreams, "
            "and forbid scope reduction, partial release, MVP, and slice mode"
        )
    required_documents = scope.get("required_documents")
    if (
        not isinstance(required_documents, list)
        or not required_documents
        or not all(isinstance(value, str) and value for value in required_documents)
    ):
        errors.append("scope.required_documents must be a non-empty list of paths")
    else:
        for path in required_documents:
            if not _path_exists(root, path):
                errors.append(f"scope required document does not exist: {path}")
    if scope.get("release_requires") != [
        "all_workstreams_complete",
        "all_ledger_items_complete",
        "automated_verification_passes",
        "live_end_to_end_evidence",
    ]:
        errors.append(
            "scope.release_requires must enforce the complete production release predicates"
        )

    all_ids: set[str] = set()
    all_items: list[tuple[str, dict[str, Any]]] = []
    for group in GROUPS:
        items = document.get(group)
        if not isinstance(items, list) or not items:
            errors.append(f"{group} must be a non-empty array")
            continue
        for item in items:
            if not isinstance(item, dict):
                errors.append(f"{group} entries must be objects")
                continue
            item_id = item.get("id")
            label = f"{group}/{item_id}"
            if not isinstance(item_id, str) or not item_id:
                errors.append(f"{label} must have a non-empty id")
            elif item_id in all_ids:
                errors.append(f"duplicate ledger id: {item_id}")
            else:
                all_ids.add(item_id)
            all_items.append((label, item))
            if not isinstance(item.get("title"), str) or not item["title"]:
                errors.append(f"{label} title must be non-empty")
            status = item.get("status")
            if status not in STATUSES:
                errors.append(f"{label} has invalid status: {status}")
            for field in ("implementation", "tests", "e2e"):
                values = item.get(field)
                if (
                    not isinstance(values, list)
                    or not values
                    or not all(isinstance(value, str) and value for value in values)
                ):
                    errors.append(f"{label} {field} must be a non-empty list of paths")
            blockers = item.get("blockers")
            if not isinstance(blockers, list) or not all(
                isinstance(value, str) and value for value in blockers
            ):
                errors.append(f"{label} blockers must be a list of non-empty strings")
                blockers = []
            if status == "complete":
                if blockers:
                    errors.append(f"{label} cannot be complete with blockers")
                commands = item.get("verification_commands")
                if not isinstance(commands, list) or not commands:
                    errors.append(f"{label} complete items require verification_commands")
                for field in ("implementation", "tests", "e2e"):
                    for path in item.get(field, []):
                        if isinstance(path, str) and not _path_exists(root, path):
                            errors.append(f"{label} complete evidence does not exist: {path}")

    workstreams = scope.get("workstreams")
    if not isinstance(workstreams, list):
        errors.append("scope.workstreams must be a non-empty list")
        workstreams = []
    workstream_ids: set[str] = set()
    referenced_items: set[str] = set()
    item_by_id = {
        item.get("id"): item
        for _, item in all_items
        if isinstance(item.get("id"), str) and item.get("id")
    }
    for workstream in workstreams:
        if not isinstance(workstream, dict):
            errors.append("scope.workstreams entries must be mappings")
            continue
        workstream_id = workstream.get("id")
        label = f"scope/workstreams/{workstream_id}"
        if not isinstance(workstream_id, str) or not workstream_id:
            errors.append(f"{label} must have a non-empty id")
            continue
        if workstream_id in workstream_ids:
            errors.append(f"duplicate workstream id: {workstream_id}")
        workstream_ids.add(workstream_id)
        if not isinstance(workstream.get("title"), str) or not workstream["title"]:
            errors.append(f"{label} title must be non-empty")
        refs = workstream.get("ledger_items")
        if (
            not isinstance(refs, list)
            or not refs
            or not all(isinstance(ref, str) and ref for ref in refs)
        ):
            errors.append(f"{label} ledger_items must be a non-empty list")
            refs = []
        for ref in refs:
            referenced_items.add(ref)
            if ref not in item_by_id:
                errors.append(f"{label} references unknown ledger item: {ref}")
        status = workstream.get("status")
        if status not in STATUSES:
            errors.append(f"{label} has invalid status: {status}")
        blockers = workstream.get("blockers")
        if not isinstance(blockers, list) or not all(
            isinstance(value, str) and value for value in blockers
        ):
            errors.append(f"{label} blockers must be a list of non-empty strings")
            blockers = []
        if status == "complete" and blockers:
            errors.append(f"{label} cannot be complete with blockers")
        if status == "complete":
            provider_e2e_evidence = workstream.get("provider_e2e_evidence")
            if (
                not isinstance(provider_e2e_evidence, list)
                or not provider_e2e_evidence
                or not all(isinstance(path, str) and path.strip() for path in provider_e2e_evidence)
            ):
                errors.append(f"{label} complete items require provider_e2e_evidence paths")
            else:
                for path in provider_e2e_evidence:
                    if not _path_exists(root, path):
                        errors.append(f"{label} provider E2E evidence does not exist: {path}")
            verification_commands = workstream.get("verification_commands")
            if not isinstance(verification_commands, list) or not verification_commands:
                errors.append(f"{label} complete items require verification_commands")
        if status == "complete" and any(
            item_by_id.get(ref, {}).get("status") != "complete" for ref in refs
        ):
            errors.append(
                f"{label} cannot be complete while referenced ledger items are incomplete"
            )
    missing_workstreams = sorted(REQUIRED_WORKSTREAMS - workstream_ids)
    if missing_workstreams:
        errors.append(
            "scope is missing required production workstreams: " + ", ".join(missing_workstreams)
        )
    unexpected_workstreams = sorted(workstream_ids - REQUIRED_WORKSTREAMS)
    if unexpected_workstreams:
        errors.append("scope contains unapproved workstreams: " + ", ".join(unexpected_workstreams))
    missing_items = sorted(str(item_id) for item_id in set(item_by_id) - referenced_items)
    if missing_items:
        errors.append("scope has orphaned ledger items: " + ", ".join(missing_items))

    if document.get("release_complete") is True:
        incomplete = [label for label, item in all_items if item.get("status") != "complete"]
        if incomplete:
            errors.append(
                "release_complete requires every ledger item to be complete: "
                + ", ".join(incomplete)
            )
        if workstream_ids != REQUIRED_WORKSTREAMS:
            errors.append("release_complete requires the exact whole-repository workstream set")
        if any(
            workstream.get("status") != "complete"
            for workstream in workstreams
            if isinstance(workstream, dict)
        ):
            errors.append("release_complete requires every production workstream to be complete")
    return errors


def _run_release_commands(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for group in GROUPS:
        for item in document.get(group, []):
            if not isinstance(item, dict) or item.get("status") != "complete":
                continue
            for command in item.get("verification_commands", []):
                if command in seen:
                    continue
                seen.add(command)
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    shell=True,
                    check=False,
                    text=True,
                    capture_output=True,
                )
                if result.returncode:
                    errors.append(
                        f"verification command failed ({result.returncode}): {command}\n"
                        f"{result.stdout[-1000:]}{result.stderr[-1000:]}"
                    )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate ledger structure without requiring release completion",
    )
    parser.add_argument(
        "--release",
        action="store_true",
        help="require a complete ledger and execute every declared verification command",
    )
    args = parser.parse_args()
    try:
        document = yaml.safe_load(LEDGER_PATH.read_text())
        if not isinstance(document, dict):
            raise ValueError("production ledger root must be a mapping")
        errors = validate_production_ledger(document, ROOT)
        if args.release and not errors:
            errors.extend(_run_release_commands(document))
            if not document.get("release_complete"):
                errors.append("--release requires release_complete: true")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"production ledger validation failed: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"production ledger validation failed: {error}", file=sys.stderr)
        return 1
    if args.release:
        print("production completion ledger is structurally valid and release-verified")
    else:
        print("production completion ledger is structurally valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
