"""Read-only, deterministic closure-package admission check."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLOSURE = ROOT / "contracts" / "closure"


def main() -> int:
    matrix = (CLOSURE / "VERIFICATION-MATRIX.md").read_text()
    required = ["| C-00", "| C-001", "| C-002", "| C-003", "| C-004", "| C-005", "| C-006", "| C-007", "| C-008", "| C-009", "| C-010", "| C-011", "| C-012"]
    assert all(item in matrix for item in required), "closure matrix is incomplete"
    assert "| open |" not in matrix.lower() and "unresolved" not in matrix.lower()
    schemas = sorted(CLOSURE.glob("*.schema.json"))
    assert len(schemas) == 12, f"expected 12 closure schemas, found {len(schemas)}"
    for schema_path in schemas:
        schema = json.loads(schema_path.read_text())
        assert schema["$schema"].endswith("2020-12/schema")
        assert schema["$id"].endswith("/v2")
        names = {"connector-capability-binding": "connector-binding", "journey-state-compiler": "journey-state", "representation-concurrency": "representation-conflict"}
        stem = schema_path.name.removesuffix('.schema.json')
        fixture = CLOSURE / "fixtures" / f"{names.get(stem, stem)}.valid.json"
        assert fixture.exists(), f"missing fixture for {schema_path.name}"
        payload = json.loads(fixture.read_text())
        for field in schema.get("required", []):
            assert field in payload, f"fixture missing {field}: {fixture.name}"
    print(f"spec closure PASS: {len(schemas)} schemas, 12 requirements, all fixtures present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

