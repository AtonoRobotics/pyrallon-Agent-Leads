"""Machine validation for the independent OT-01 completion ledger."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def validate_completion_ledger(document: dict[str, Any], root: Path) -> list[str]:
    """Return bounded validation errors without treating incompleteness as completion."""
    errors: list[str] = []
    sections = document.get("sections")
    if not isinstance(sections, list):
        return ["sections must be an array"]
    section_ids = [section.get("id") for section in sections if isinstance(section, dict)]
    if set(section_ids) != {str(number) for number in range(1, 20)}:
        errors.append("section ids must be exactly 1 through 19")
    if len(section_ids) != len(set(section_ids)):
        errors.append("section ids must be unique")

    design_truth_path = root / "DESIGN-TRUTH-LEDGER.md"
    blocker_pattern = r"\b(?:OPEN-\d{3}|GAP-[A-Z0-9]+(?:-[A-Z0-9]+)*)\b"
    declared_blockers = (
        set(re.findall(blocker_pattern, design_truth_path.read_text()))
        if design_truth_path.is_file()
        else set()
    )
    if not declared_blockers:
        errors.append("DESIGN-TRUTH-LEDGER.md must declare at least one blocker")

    for section in sections:
        if not isinstance(section, dict):
            errors.append("each section must be an object")
            continue
        section_id = section.get("id")
        status = section.get("status")
        evidence = section.get("evidence")
        blockers = section.get("blockers")
        if status not in {"complete", "in_progress", "blocked", "not_started"}:
            errors.append(f"section {section_id} has an invalid status")
        if not isinstance(evidence, list) or not all(
            isinstance(reference, str) and reference for reference in evidence
        ):
            errors.append(f"section {section_id} evidence must be non-empty strings")
            evidence = []
        if not isinstance(blockers, list) or not all(
            isinstance(blocker, str) and re.fullmatch(blocker_pattern, blocker)
            for blocker in blockers
        ):
            errors.append(f"section {section_id} blockers must be declared blocker identifiers")
            blockers = []
        if status == "complete" and blockers:
            errors.append(f"complete section {section_id} must not declare blockers")
        if status == "complete" and not evidence:
            errors.append(f"complete section {section_id} must cite evidence")
        if status == "blocked" and not blockers:
            errors.append(f"blocked section {section_id} must name at least one blocker")
        unknown_blockers = sorted(set(blockers) - declared_blockers)
        if unknown_blockers:
            errors.append(f"section {section_id} uses undeclared blockers: {unknown_blockers}")
        for reference in evidence:
            relative_path = reference.split("::", maxsplit=1)[0]
            if Path(relative_path).is_absolute() or not (root / relative_path).is_file():
                errors.append(f"section {section_id} evidence does not exist: {reference}")

    if (
        document.get("activatable") is True
        and isinstance(sections, list)
        and any(
            isinstance(section, dict) and section.get("status") != "complete"
            for section in sections
        )
    ):
        errors.append("activatable must be false until every section is complete")
    return errors
