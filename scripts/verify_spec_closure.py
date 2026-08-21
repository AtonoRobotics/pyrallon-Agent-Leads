"""Read-only, deterministic closure-package admission check."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
CLOSURE = ROOT / "contracts" / "closure"


def main() -> int:
    matrix = (CLOSURE / "VERIFICATION-MATRIX.md").read_text()
    required_ids = {f"C-{number:03d}" for number in range(1, 13)}
    rows = {
        line.split("|", 2)[1].strip(): line
        for line in matrix.splitlines()
        if line.startswith("| C-")
    }
    assert set(rows) == required_ids, "closure matrix is incomplete"
    assert all(row.rstrip().split("|")[-2].strip() == "closed" for row in rows.values()), (
        "closure matrix contains an unclosed requirement"
    )
    assert "| open |" not in matrix.lower() and "unresolved" not in matrix.lower()
    schemas = sorted(CLOSURE.glob("*.schema.json"))
    assert len(schemas) == 12, f"expected 12 closure schemas, found {len(schemas)}"
    names = {
        "connector-capability-binding": "connector-binding",
        "journey-state-compiler": "journey-state",
        "representation-concurrency": "representation-conflict",
    }
    for schema_path in schemas:
        schema = json.loads(schema_path.read_text())
        assert schema["$schema"].endswith("2020-12/schema")
        assert schema["$id"].endswith("/v2")
        Draft202012Validator.check_schema(schema)
        stem = schema_path.name.removesuffix(".schema.json")
        fixture = CLOSURE / "fixtures" / f"{names.get(stem, stem)}.valid.json"
        assert fixture.exists(), f"missing fixture for {schema_path.name}"
        payload = json.loads(fixture.read_text())
        errors = sorted(
            Draft202012Validator(schema).iter_errors(payload), key=lambda error: error.path
        )
        assert not errors, f"fixture does not validate: {fixture.name}: {errors[0].message}"
    print(f"spec closure PASS: {len(schemas)} schemas, 12 requirements, all fixtures present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
