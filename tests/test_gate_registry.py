from pathlib import Path

import yaml


def test_gate_registry_is_unique_and_dependency_closed() -> None:
    registry = yaml.safe_load(Path("PRODUCTION-GATE-REGISTRY.yaml").read_text())
    gates = registry["gates"]
    identifiers = [gate["id"] for gate in gates]
    assert len(identifiers) == len(set(identifiers)) == 35
    known = set(identifiers)
    for gate in gates:
        assert set(gate.get("dependencies", [])) <= known

