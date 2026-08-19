from pathlib import Path

import yaml

from buyer_ops_contracts.gate_registry import validate_registry


def test_gate_registry_is_unique_and_dependency_closed() -> None:
    registry = yaml.safe_load(Path("PRODUCTION-GATE-REGISTRY.yaml").read_text())
    gates = registry["gates"]
    identifiers = [gate["id"] for gate in gates]
    assert len(identifiers) == len(set(identifiers)) == 35
    known = set(identifiers)
    for gate in gates:
        assert set(gate.get("dependencies", [])) <= known


def test_governing_gate_registry_passes_machine_validation() -> None:
    registry = yaml.safe_load(Path("PRODUCTION-GATE-REGISTRY.yaml").read_text())
    assert validate_registry(registry) == []


def test_gate_registry_rejects_dependency_cycles() -> None:
    registry = yaml.safe_load(Path("PRODUCTION-GATE-REGISTRY.yaml").read_text())
    registry["gates"][0]["dependencies"] = ["GATE-035"]
    registry["gates"][-1]["dependencies"] = ["GATE-001"]
    assert "gate dependency graph must be acyclic" in validate_registry(registry)
