from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CompatibilityFinding:
    path: str
    rule: str
    message: str
    breaking: bool


def compare_schemas(
    previous: dict[str, Any], current: dict[str, Any]
) -> list[CompatibilityFinding]:
    """Conservative reader-compatibility analysis; unknown changes require review."""
    findings: list[CompatibilityFinding] = []

    def walk(old: Any, new: Any, path: str) -> None:
        if not isinstance(old, dict) or not isinstance(new, dict):
            if old != new:
                findings.append(
                    CompatibilityFinding(path, "VALUE_CHANGED", "schema constraint changed", True)
                )
            return
        old_required, new_required = set(old.get("required", [])), set(new.get("required", []))
        for name in sorted(new_required - old_required):
            findings.append(
                CompatibilityFinding(
                    f"{path}.required", "REQUIRED_ADDED", f"required property added: {name}", True
                )
            )
        old_enum, new_enum = old.get("enum"), new.get("enum")
        if isinstance(old_enum, list) and isinstance(new_enum, list):
            removed = set(map(str, old_enum)) - set(map(str, new_enum))
            if removed:
                findings.append(
                    CompatibilityFinding(
                        f"{path}.enum", "ENUM_NARROWED", f"values removed: {sorted(removed)}", True
                    )
                )
        if old.get("type") != new.get("type") and "type" in old and "type" in new:
            findings.append(
                CompatibilityFinding(f"{path}.type", "TYPE_CHANGED", "type changed", True)
            )
        if (
            old.get("additionalProperties", True) is True
            and new.get("additionalProperties", True) is False
        ):
            findings.append(
                CompatibilityFinding(
                    path, "UNKNOWN_FIELDS_REJECTED", "additional properties became forbidden", True
                )
            )
        old_props, new_props = old.get("properties", {}), new.get("properties", {})
        for name in sorted(set(old_props) - set(new_props)):
            findings.append(
                CompatibilityFinding(
                    f"{path}.properties.{name}", "PROPERTY_REMOVED", "property removed", True
                )
            )
        for name in sorted(set(old_props) & set(new_props)):
            walk(old_props[name], new_props[name], f"{path}.properties.{name}")
        old_defs, new_defs = old.get("$defs", {}), new.get("$defs", {})
        for name in sorted(set(old_defs) - set(new_defs)):
            findings.append(
                CompatibilityFinding(
                    f"{path}.$defs.{name}", "DEFINITION_REMOVED", "definition removed", True
                )
            )
        for name in sorted(set(old_defs) & set(new_defs)):
            walk(old_defs[name], new_defs[name], f"{path}.$defs.{name}")

    walk(previous, current, "$")
    return findings

