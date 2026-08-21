import importlib.util
import re
from pathlib import Path

import yaml

_SPEC = importlib.util.spec_from_file_location(
    "verify_production_ledger", Path("scripts/verify_production_ledger.py")
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
validate_production_ledger = _MODULE.validate_production_ledger

_SCOPE_SPEC = importlib.util.spec_from_file_location(
    "verify_production_scope", Path("scripts/verify_production_scope.py")
)
assert _SCOPE_SPEC is not None and _SCOPE_SPEC.loader is not None
_SCOPE_MODULE = importlib.util.module_from_spec(_SCOPE_SPEC)
_SCOPE_SPEC.loader.exec_module(_SCOPE_MODULE)
validate_production_scope = _SCOPE_MODULE.validate_production_scope
validate_execution_mandate = _SCOPE_MODULE.validate_execution_mandate

_EXECUTION_SPEC = importlib.util.spec_from_file_location(
    "verify_production_execution", Path("scripts/verify_production_execution.py")
)
assert _EXECUTION_SPEC is not None and _EXECUTION_SPEC.loader is not None
_EXECUTION_MODULE = importlib.util.module_from_spec(_EXECUTION_SPEC)
_EXECUTION_SPEC.loader.exec_module(_EXECUTION_MODULE)
validate_execution_state = _EXECUTION_MODULE.validate_execution_state

_FOCUS_SPEC = importlib.util.spec_from_file_location(
    "verify_production_focus", Path("scripts/verify_production_focus.py")
)
assert _FOCUS_SPEC is not None and _FOCUS_SPEC.loader is not None
_FOCUS_MODULE = importlib.util.module_from_spec(_FOCUS_SPEC)
_FOCUS_SPEC.loader.exec_module(_FOCUS_MODULE)
validate_focus = _FOCUS_MODULE.validate_focus
workstream_touchpoints = _FOCUS_MODULE.WORKSTREAM_TOUCHPOINTS


def _item(tmp_path: Path, *, status: str = "complete") -> dict:
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("verified")
    return {
        "id": "X-1",
        "title": "One requirement",
        "implementation": ["evidence.txt"],
        "tests": ["evidence.txt"],
        "e2e": ["evidence.txt"],
        "status": status,
        "blockers": [] if status == "complete" else ["unfinished"],
        "verification_commands": ["true"] if status == "complete" else [],
    }


def _scope() -> dict:
    ids = {
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
    return {
        "mode": "whole_repository_production",
        "required_documents": ["evidence.txt"],
        "release_requires": [
            "all_workstreams_complete",
            "all_ledger_items_complete",
            "automated_verification_passes",
            "live_end_to_end_evidence",
        ],
        "workstreams": [
            {
                "id": value,
                "title": value,
                "ledger_items": ["X-1"],
                "status": "complete",
                "blockers": [],
                "provider_e2e_evidence": ["evidence.txt"],
                "verification_commands": ["true"],
            }
            for value in ids
        ],
    }


def test_production_ledger_requires_every_group_and_release_completion() -> None:
    ledger = yaml.safe_load(Path("PRODUCTION-COMPLETION-LEDGER.yaml").read_text())

    errors = validate_production_ledger(ledger, Path("."))

    assert errors == []
    assert ledger["release_complete"] is False


def test_production_ledger_tracks_every_functional_requirement_in_the_prd() -> None:
    ledger = yaml.safe_load(Path("PRODUCTION-COMPLETION-LEDGER.yaml").read_text())
    specification = Path(ledger["specification"]).read_text()
    expected = {
        f"FR-{int(value):02d}"
        for value in re.findall(r"^### FR-(\d+)\b", specification, re.MULTILINE)
    }
    actual = {item["id"] for item in ledger["requirements"]}
    assert expected <= actual


def test_scope_requires_exact_whole_repository_execution_policy() -> None:
    ledger = yaml.safe_load(Path("PRODUCTION-COMPLETION-LEDGER.yaml").read_text())
    assert validate_production_scope(ledger) == []


def test_scope_rejects_reduced_execution_mode() -> None:
    ledger = yaml.safe_load(Path("PRODUCTION-COMPLETION-LEDGER.yaml").read_text())
    ledger["scope"]["execution_policy"]["allow_mvp_or_slice_mode"] = True
    assert validate_production_scope(ledger)


def test_execution_mandate_requires_every_workstream() -> None:
    mandate = yaml.safe_load(Path("PRODUCTION-EXECUTION-MANDATE.yaml").read_text())
    assert validate_execution_mandate(mandate) == []


def test_execution_mandate_rejects_removed_workstream() -> None:
    mandate = yaml.safe_load(Path("PRODUCTION-EXECUTION-MANDATE.yaml").read_text())
    mandate["required_workstreams"] = mandate["required_workstreams"][:-1]
    assert validate_execution_mandate(mandate)


def test_execution_mandate_rejects_slice_mode() -> None:
    mandate = yaml.safe_load(Path("PRODUCTION-EXECUTION-MANDATE.yaml").read_text())
    mandate["prohibited_modes"].remove("slice")
    assert validate_execution_mandate(mandate)


def test_execution_cursor_is_whole_repository_and_canonical() -> None:
    state = yaml.safe_load(Path("PRODUCTION-EXECUTION-STATE.yaml").read_text())
    ledger = yaml.safe_load(Path("PRODUCTION-COMPLETION-LEDGER.yaml").read_text())
    mandate = yaml.safe_load(Path("PRODUCTION-EXECUTION-MANDATE.yaml").read_text())
    assert validate_execution_state(state, ledger, mandate) == []


def test_execution_cursor_rejects_skipped_workstream() -> None:
    state = yaml.safe_load(Path("PRODUCTION-EXECUTION-STATE.yaml").read_text())
    ledger = yaml.safe_load(Path("PRODUCTION-COMPLETION-LEDGER.yaml").read_text())
    mandate = yaml.safe_load(Path("PRODUCTION-EXECUTION-MANDATE.yaml").read_text())
    state["remaining_workstreams"] = state["remaining_workstreams"][1:]
    assert any("partition" in error for error in validate_execution_state(state, ledger, mandate))


def test_execution_cursor_rejects_out_of_order_current_workstream() -> None:
    state = yaml.safe_load(Path("PRODUCTION-EXECUTION-STATE.yaml").read_text())
    ledger = yaml.safe_load(Path("PRODUCTION-COMPLETION-LEDGER.yaml").read_text())
    mandate = yaml.safe_load(Path("PRODUCTION-EXECUTION-MANDATE.yaml").read_text())
    state["current_workstream"] = "ios_client_and_offline_contract"
    assert any(
        "first remaining" in error for error in validate_execution_state(state, ledger, mandate)
    )


def test_execution_cursor_rejects_weakened_completion_mandate() -> None:
    state = yaml.safe_load(Path("PRODUCTION-EXECUTION-STATE.yaml").read_text())
    ledger = yaml.safe_load(Path("PRODUCTION-COMPLETION-LEDGER.yaml").read_text())
    mandate = yaml.safe_load(Path("PRODUCTION-EXECUTION-MANDATE.yaml").read_text())
    mandate["completion_rule"] = "all_required_workstreams"
    errors = validate_execution_state(state, ledger, mandate)
    assert any("completion_rule" in error for error in errors)


def test_execution_cursor_rejects_weakened_required_workstream_proof() -> None:
    state = yaml.safe_load(Path("PRODUCTION-EXECUTION-STATE.yaml").read_text())
    ledger = yaml.safe_load(Path("PRODUCTION-COMPLETION-LEDGER.yaml").read_text())
    mandate = yaml.safe_load(Path("PRODUCTION-EXECUTION-MANDATE.yaml").read_text())
    mandate["required_workstreams"][0]["completion"] = "implementation"
    errors = validate_execution_state(state, ledger, mandate)
    assert any("provider E2E" in error for error in errors)


def test_execution_cursor_rejects_generic_next_action() -> None:
    state = yaml.safe_load(Path("PRODUCTION-EXECUTION-STATE.yaml").read_text())
    ledger = yaml.safe_load(Path("PRODUCTION-COMPLETION-LEDGER.yaml").read_text())
    mandate = yaml.safe_load(Path("PRODUCTION-EXECUTION-MANDATE.yaml").read_text())
    state["next_action"] = "implement and provider-test the current workstream completely"
    errors = validate_execution_state(state, ledger, mandate)
    assert any("generic workstream placeholders" in error for error in errors)


def test_execution_cursor_requires_provider_e2e_evidence_before_advancement() -> None:
    state = yaml.safe_load(Path("PRODUCTION-EXECUTION-STATE.yaml").read_text())
    ledger = yaml.safe_load(Path("PRODUCTION-COMPLETION-LEDGER.yaml").read_text())
    mandate = yaml.safe_load(Path("PRODUCTION-EXECUTION-MANDATE.yaml").read_text())
    completed = state["remaining_workstreams"][0]
    state["completed_workstreams"] = [completed]
    state["remaining_workstreams"] = state["remaining_workstreams"][1:]
    state["current_workstream"] = state["remaining_workstreams"][0]
    ledger_workstream = next(
        item for item in ledger["scope"]["workstreams"] if item["id"] == completed
    )
    ledger_workstream["status"] = "complete"
    errors = validate_execution_state(state, ledger, mandate)
    assert any("provider_e2e_evidence" in error for error in errors)


def test_repository_has_mandatory_production_guard() -> None:
    hook = Path(".githooks/pre-commit")
    push_hook = Path(".githooks/pre-push")
    workflow = Path(".github/workflows/contracts.yml").read_text()
    assert hook.is_file()
    assert push_hook.is_file()
    assert "scripts/production_guard.py" in hook.read_text()
    assert "scripts/production_guard.py" in push_hook.read_text()
    assert "Run mandatory whole-repository production guard" in workflow
    assert "scripts/production_guard.py" in workflow


def test_pre_commit_locks_changes_to_active_workstream() -> None:
    hook = Path(".githooks/pre-commit").read_text()
    assert "scripts/verify_production_focus.py" in hook


def test_focus_rejects_later_workstream_only_changes() -> None:
    state = yaml.safe_load(Path("PRODUCTION-EXECUTION-STATE.yaml").read_text())
    errors = validate_focus(state, {"src/buyer_ops_contracts/connector_service.py"})
    assert any("later-workstream drift" in error for error in errors)


def test_focus_accepts_active_workstream_changes() -> None:
    state = yaml.safe_load(Path("PRODUCTION-EXECUTION-STATE.yaml").read_text())
    errors = validate_focus(state, {"src/buyer_ops_contracts/calendar_runtime.py"})
    assert errors == []


def test_focus_defines_touchpoints_for_every_execution_workstream() -> None:
    state = yaml.safe_load(Path("PRODUCTION-EXECUTION-STATE.yaml").read_text())
    sequence = state["canonical_sequence"]
    assert set(workstream_touchpoints) == set(sequence)
    assert all(workstream_touchpoints[name] for name in sequence)


def test_production_deployment_runs_one_shot_migration_fresh() -> None:
    readme = Path("README.md").read_text()
    assert "up -d postgres migrate" not in readme
    assert "run --rm migrate" in readme


def test_release_completion_rejects_incomplete_items(tmp_path: Path) -> None:
    ledger = {
        "version": 1,
        "project": "buyer-operations",
        "specification": "evidence.txt",
        "release_complete": True,
        "scope": _scope(),
        "requirements": [_item(tmp_path, status="incomplete")],
        "packets": [_item(tmp_path)],
        "surfaces": [_item(tmp_path)],
    }

    errors = validate_production_ledger(ledger, tmp_path)

    assert any("release_complete requires every ledger item" in error for error in errors)


def test_complete_item_requires_real_evidence_and_verification(tmp_path: Path) -> None:
    ledger = {
        "version": 1,
        "project": "buyer-operations",
        "specification": "missing-spec.md",
        "release_complete": False,
        "scope": _scope(),
        "requirements": [_item(tmp_path)],
        "packets": [_item(tmp_path)],
        "surfaces": [_item(tmp_path)],
    }

    errors = validate_production_ledger(ledger, tmp_path)

    assert "specification does not exist: missing-spec.md" in errors


def test_scope_rejects_slice_mode_and_orphaned_ledger_items(tmp_path: Path) -> None:
    ledger = {
        "version": 1,
        "project": "buyer-operations",
        "specification": "evidence.txt",
        "release_complete": False,
        "scope": _scope() | {"mode": "mvp"},
        "requirements": [_item(tmp_path)],
        "packets": [{**_item(tmp_path), "id": "P-1"}],
        "surfaces": [{**_item(tmp_path), "id": "S-1"}],
    }

    errors = validate_production_ledger(ledger, tmp_path)

    assert "scope.mode must be whole_repository_production" in errors
    assert any("scope has orphaned ledger items" in error for error in errors)


def test_scope_rejects_missing_required_workstream(tmp_path: Path) -> None:
    scope = _scope()
    scope["workstreams"] = [
        workstream
        for workstream in scope["workstreams"]
        if workstream["id"] != "ios_client_and_offline_contract"
    ]
    ledger = {
        "version": 1,
        "project": "buyer-operations",
        "specification": "evidence.txt",
        "release_complete": False,
        "scope": scope,
        "requirements": [_item(tmp_path)],
        "packets": [{**_item(tmp_path), "id": "P-1"}],
        "surfaces": [{**_item(tmp_path), "id": "S-1"}],
    }

    errors = validate_production_ledger(ledger, tmp_path)

    assert any("ios_client_and_offline_contract" in error for error in errors)


def test_complete_workstream_requires_provider_e2e_artifact(tmp_path: Path) -> None:
    scope = _scope()
    scope["workstreams"][0].pop("provider_e2e_evidence")
    ledger = {
        "version": 1,
        "project": "buyer-operations",
        "specification": "evidence.txt",
        "release_complete": False,
        "scope": scope,
        "requirements": [_item(tmp_path)],
        "packets": [_item(tmp_path)],
        "surfaces": [_item(tmp_path)],
    }

    errors = validate_production_ledger(ledger, tmp_path)

    assert any("provider_e2e_evidence" in error for error in errors)


def test_complete_workstream_rejects_missing_provider_e2e_artifact(tmp_path: Path) -> None:
    scope = _scope()
    scope["workstreams"][0]["provider_e2e_evidence"] = ["missing-evidence.json"]
    ledger = {
        "version": 1,
        "project": "buyer-operations",
        "specification": "evidence.txt",
        "release_complete": False,
        "scope": scope,
        "requirements": [_item(tmp_path)],
        "packets": [_item(tmp_path)],
        "surfaces": [_item(tmp_path)],
    }

    errors = validate_production_ledger(ledger, tmp_path)

    assert any("provider E2E evidence does not exist" in error for error in errors)
