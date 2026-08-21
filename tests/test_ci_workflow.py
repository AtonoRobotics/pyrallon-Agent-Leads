import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMMUTABLE_ACTION = re.compile(r"^\s*- uses: [^@\s]+@[0-9a-f]{40}(?:\s+#\s+.+)?$")


def test_every_github_action_is_pinned_to_an_immutable_commit() -> None:
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
    assert workflows
    action_lines = [
        line
        for workflow in workflows
        for line in workflow.read_text().splitlines()
        if "- uses:" in line
    ]
    assert action_lines
    assert all(IMMUTABLE_ACTION.fullmatch(line) for line in action_lines)


def test_contract_ci_builds_both_distribution_artifacts() -> None:
    workflow = (ROOT / ".github" / "workflows" / "contracts.yml").read_text()
    assert "uv build --sdist --wheel" in workflow


def test_contract_ci_executes_the_built_wheel_outside_the_source_tree() -> None:
    workflow = (ROOT / ".github" / "workflows" / "contracts.yml").read_text()
    assert "Smoke-test installed wheel resources" in workflow
    assert 'PYTHONPATH="$WHEEL_PATH"' in workflow
    assert "load_metric_catalog" in workflow
    assert "working-directory: /tmp" in workflow


def test_contract_ci_enforces_whole_repository_scope_before_tests() -> None:
    workflow = (ROOT / ".github" / "workflows" / "contracts.yml").read_text()
    scope_check = "uv run python scripts/verify_production_scope.py"
    assert scope_check in workflow
    assert workflow.index(scope_check) < workflow.index("uv run pytest")
