"""Reject commits that work only on a later production workstream."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "PRODUCTION-EXECUTION-STATE.yaml"

# These are deliberately broad implementation/test touchpoints.  A commit may
# include cross-cutting work, but it must also advance the active cursor.
WORKSTREAM_TOUCHPOINTS = {
    "calendar_and_esignature": (
        "src/buyer_ops_contracts/calendar",
        "src/buyer_ops_contracts/calendar_",
        "src/buyer_ops_contracts/esignature",
        "src/buyer_ops_contracts/provider_adapters.py",
        "src/buyer_ops_contracts/worker_main.py",
        "tests/test_calendar",
        "tests/test_esignature",
        "tests/test_provider_adapters.py",
        "scripts/run_live_production_e2e.py",
        "scripts/run_live_calendar_esignature_e2e.py",
        "scripts/verify_provider_production_readiness.py",
        "tests/test_provider_production_readiness.py",
        "compose.production.yml",
    ),
    "cognitive_production_routing": (
        "src/buyer_ops_contracts/cognitive",
        "src/buyer_ops_contracts/cognition",
        "src/buyer_ops_contracts/cognitive_service.py",
        "src/buyer_ops_contracts/cognitive_credentials_runtime.py",
        "scripts/run_live_cognitive_e2e.py",
        "tests/test_cognitive",
    ),
    "connectors_and_webhooks": (
        "src/buyer_ops_contracts/connector",
        "src/buyer_ops_contracts/ingress",
        "src/buyer_ops_contracts/provider_adapters.py",
        "src/buyer_ops_contracts/gateway_runtime.py",
        "tests/test_connector",
        "tests/test_ingress",
        "tests/test_provider_adapters.py",
    ),
    "deployment_and_release_operations": (
        "compose.production.yml",
        "Dockerfile",
        ".env.production.example",
        "scripts/apply_migrations.py",
        "scripts/check_temporal.py",
        "scripts/postgres_backup_restore.py",
        "scripts/production_guard.py",
        "scripts/verify_migrations.py",
        "tests/test_postgres_backup_restore.py",
    ),
    "domain_and_workflows": (
        "src/buyer_ops_contracts/journey_state.py",
        "src/buyer_ops_contracts/qualification_runtime.py",
        "src/buyer_ops_contracts/consultation_runtime.py",
        "src/buyer_ops_contracts/temporal_workflows.py",
        "src/buyer_ops_contracts/worker_main.py",
        "tests/test_journey_state.py",
        "tests/test_temporal_workflow.py",
    ),
    "ios_client_and_offline_contract": (
        "ios/",
        "scripts/verify_ios_surface.py",
        "tests/e2e/",
    ),
    "nurture_and_transaction_operations": (
        "src/buyer_ops_contracts/nurture",
        "src/buyer_ops_contracts/transaction",
        "src/buyer_ops_contracts/reminder_runtime.py",
        "tests/test_nurture_runtime.py",
        "tests/test_transaction_runtime.py",
        "tests/test_reminder_runtime.py",
    ),
    "operator_api_and_web_ui": (
        "src/buyer_ops_contracts/control_plane.py",
        "src/buyer_ops_contracts/operator_",
        "ui/",
        "tests/test_control_plane.py",
        "tests/e2e/",
    ),
    "postgres_backup_and_restore": (
        "scripts/postgres_backup_restore.py",
        "migrations/",
        "tests/test_postgres_backup_restore.py",
        "tests/test_postgres_integration.py",
    ),
    "provider_ingress_and_senders": (
        "src/buyer_ops_contracts/ingress_runtime.py",
        "src/buyer_ops_contracts/provider_adapters.py",
        "src/buyer_ops_contracts/voice_runtime.py",
        "src/buyer_ops_contracts/voice_repository.py",
        "tests/test_ingress_runtime.py",
        "tests/test_voice_runtime.py",
    ),
    "security_observability_and_evaluations": (
        "src/buyer_ops_contracts/telemetry.py",
        "src/buyer_ops_contracts/evaluations.py",
        "scripts/run_evaluations.py",
        "evaluations/",
        "tests/test_evaluations.py",
        "tests/test_telemetry.py",
    ),
    "temporal_replay_and_recovery": (
        "src/buyer_ops_contracts/temporal_workflows.py",
        "src/buyer_ops_contracts/worker_main.py",
        "scripts/check_temporal.py",
        "tests/test_temporal_workflow.py",
        "tests/test_worker_configuration.py",
    ),
}


def _changed_paths() -> set[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    paths: set[str] = set()
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        value = line[3:]
        if " -> " in value:
            value = value.rsplit(" -> ", 1)[-1]
        paths.add(value)
    return paths


def validate_focus(state: dict[str, object], changed_paths: set[str]) -> list[str]:
    current = state.get("current_workstream")
    if not isinstance(current, str) or not current:
        return ["current_workstream must be set before implementation can proceed"]
    touchpoints = WORKSTREAM_TOUCHPOINTS.get(current)
    if touchpoints is None:
        return [f"no focus touchpoints are defined for active workstream: {current}"]
    if not changed_paths:
        return ["the active production workstream has no implementation changes"]

    def matches(path: str, touchpoint: str) -> bool:
        if touchpoint.endswith("_"):
            return path.startswith(touchpoint)
        return path == touchpoint or path.startswith(touchpoint + "/")

    if not any(matches(path, touchpoint) for path in changed_paths for touchpoint in touchpoints):
        return [
            "working tree changes do not touch the active production workstream "
            f"({current}); later-workstream drift is rejected"
        ]
    return []


def main() -> int:
    try:
        state = yaml.safe_load(STATE_PATH.read_text())
        errors = validate_focus(state if isinstance(state, dict) else {}, _changed_paths())
    except (OSError, subprocess.SubprocessError, yaml.YAMLError) as exc:
        print(f"production focus validation failed: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"production focus validation failed: {error}", file=sys.stderr)
        return 1
    print("active production workstream focus is mechanically enforced")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
