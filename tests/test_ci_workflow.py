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
