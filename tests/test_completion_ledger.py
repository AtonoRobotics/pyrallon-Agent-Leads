from pathlib import Path

import yaml

from buyer_ops_contracts.completion_ledger import validate_completion_ledger


def test_completion_ledger_rejects_activation_while_any_section_is_incomplete() -> None:
    ledger = {
        "contract": "ot01/1.0.0",
        "activatable": True,
        "sections": [
            {
                "id": str(section),
                "status": "in_progress" if section == 6 else "complete",
                "evidence": ["README.md"],
                "blockers": ["OPEN-011"] if section == 6 else [],
            }
            for section in range(1, 20)
        ],
    }

    assert (
        "activatable must be false until every section is complete"
        in validate_completion_ledger(ledger, Path("."))
    )


def test_completion_ledger_requires_exact_sections_evidence_and_named_blocks(
    tmp_path: Path,
) -> None:
    ledger = {
        "contract": "ot01/1.0.0",
        "activatable": False,
        "sections": [
            {
                "id": "1",
                "status": "complete",
                "evidence": ["missing.md"],
                "blockers": ["OPEN-999"],
            },
            {"id": "1", "status": "blocked", "evidence": [], "blockers": []},
        ],
    }

    errors = validate_completion_ledger(ledger, tmp_path)

    assert "section ids must be exactly 1 through 19" in errors
    assert "section ids must be unique" in errors
    assert "section 1 evidence does not exist: missing.md" in errors
    assert "complete section 1 must not declare blockers" in errors
    assert "blocked section 1 must name at least one blocker" in errors


def test_governing_ot01_completion_ledger_is_internally_honest() -> None:
    ledger = yaml.safe_load(Path("OT01-COMPLETION-LEDGER.yaml").read_text())

    assert validate_completion_ledger(ledger, Path(".")) == []


def test_completion_ledger_rejects_complete_section_without_evidence() -> None:
    ledger = {
        "contract": "ot01/1.0.0",
        "activatable": False,
        "sections": [
            {"id": str(section), "status": "complete", "evidence": [], "blockers": []}
            for section in range(1, 20)
        ],
    }

    assert "complete section 1 must cite evidence" in validate_completion_ledger(ledger, Path("."))


def test_completion_ledger_rejects_undeclared_blocker(tmp_path: Path) -> None:
    (tmp_path / "DESIGN-TRUTH-LEDGER.md").write_text("| OPEN-001 | declared |\n")
    ledger = {
        "contract": "ot01/1.0.0",
        "activatable": False,
        "sections": [
            {
                "id": str(section),
                "status": "blocked",
                "evidence": [],
                "blockers": ["OPEN-999" if section == 1 else "OPEN-001"],
            }
            for section in range(1, 20)
        ],
    }

    assert "section 1 uses undeclared blockers: ['OPEN-999']" in validate_completion_ledger(
        ledger, tmp_path
    )
