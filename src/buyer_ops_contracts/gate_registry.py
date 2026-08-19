from __future__ import annotations

from typing import Any

EXPECTED_GATE_IDS = tuple(f"GATE-{number:03d}" for number in range(1, 36))
REQUIRED_GATE_FIELDS = {
    "id",
    "title",
    "class",
    "scope",
    "source",
    "applies_when",
    "blocks",
    "dependencies",
    "evidence",
    "pass_condition",
}


def _dependency_cycle(gates: list[dict[str, Any]]) -> bool:
    dependencies = {gate["id"]: set(gate.get("dependencies", [])) for gate in gates}
    remaining = set(dependencies)
    while remaining:
        ready = {gate_id for gate_id in remaining if not (dependencies[gate_id] & remaining)}
        if not ready:
            return True
        remaining -= ready
    return False


def validate_registry(registry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if registry.get("registry_version") != "1.0.0":
        errors.append("registry_version must be 1.0.0")
    if registry.get("status") != "governing":
        errors.append("status must be governing")

    classes = registry.get("classes")
    if not isinstance(classes, dict):
        errors.append("classes must be a mapping")
        classes = {}
    gates = registry.get("gates")
    if not isinstance(gates, list):
        return [*errors, "gates must be a list"]
    if len(gates) != len(EXPECTED_GATE_IDS):
        errors.append(f"expected {len(EXPECTED_GATE_IDS)} gates, found {len(gates)}")

    identifiers: list[str] = []
    normalized_gates: list[dict[str, Any]] = []
    for index, gate in enumerate(gates):
        if not isinstance(gate, dict):
            errors.append(f"gate at index {index} must be a mapping")
            continue
        gate_id = gate.get("id")
        if not isinstance(gate_id, str):
            errors.append(f"gate at index {index} has a non-string id")
            identifiers.append("")
            continue
        identifiers.append(gate_id)
        normalized_gates.append(gate)
        missing = REQUIRED_GATE_FIELDS - gate.keys()
        if missing:
            errors.append(f"{gate_id} missing fields: {sorted(missing)}")
        if gate.get("class") not in classes:
            errors.append(f"{gate_id} uses an undeclared class: {gate.get('class')!r}")
        for field in ("blocks", "dependencies", "evidence"):
            value = gate.get(field)
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                errors.append(f"{gate_id}.{field} must be a list of strings")
        for field in ("title", "scope", "source", "applies_when", "pass_condition"):
            if not isinstance(gate.get(field), str) or not gate[field].strip():
                errors.append(f"{gate_id}.{field} must be a non-empty string")

    if identifiers != list(EXPECTED_GATE_IDS):
        errors.append("gate ids must be exactly GATE-001 through GATE-035 in order")

    known = set(identifiers)
    for gate in normalized_gates:
        unknown = set(gate.get("dependencies", [])) - known
        if unknown:
            errors.append(f"{gate['id']} has unknown dependencies: {sorted(unknown)}")
    if len(normalized_gates) == len(gates) and _dependency_cycle(normalized_gates):
        errors.append("gate dependency graph must be acyclic")
    return errors
