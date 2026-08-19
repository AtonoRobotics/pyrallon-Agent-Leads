"""Generate one structural golden and one requiredness failure for every ontology root."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "ONTOLOGY-V0.schema.json"
OUTPUT = ROOT / "tests" / "fixtures" / "generated"


def merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(left)
    for key, value in right.items():
        if key == "properties":
            result.setdefault(key, {}).update(value)
        elif key == "required":
            result[key] = list(dict.fromkeys(result.get(key, []) + value))
        else:
            result[key] = copy.deepcopy(value)
    return result


def generate(schema: dict[str, Any], root: dict[str, Any], field: str = "") -> Any:
    if "if" in schema and "then" in schema:
        return {}
    if "$ref" in schema:
        return generate(root["$defs"][schema["$ref"].rsplit("/", 1)[-1]], root, field)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "allOf" in schema and "properties" not in schema:
        materialized: dict[str, Any] = {}
        for part in schema["allOf"]:
            value = generate(part, root, field)
            if isinstance(value, dict):
                materialized.update(value)
        return materialized
    if "oneOf" in schema:
        return generate(schema["oneOf"][0], root, field)
    if "anyOf" in schema and schema.get("type") != "object":
        return generate(schema["anyOf"][0], root, field)
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        concrete = next(item for item in schema_type if item != "null")
        return generate({**schema, "type": concrete}, root, field)
    if schema_type == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        required = list(schema.get("required", []))
        if "anyOf" in schema:
            required.extend(schema["anyOf"][0].get("required", []))
        return {
            name: generate(properties[name], root, name)
            for name in dict.fromkeys(required)
            if name in properties
        }
    if schema_type == "array":
        count = max(1, schema.get("minItems", 0))
        values = [generate(schema.get("items", {}), root, field) for _ in range(count)]
        if schema.get("uniqueItems") and len(values) > 1 and isinstance(values[0], str):
            values = [f"{value}-{index}" for index, value in enumerate(values, 1)]
        return values
    if schema_type == "integer":
        return max(1, schema.get("minimum", 0))
    if schema_type == "number":
        return schema.get("minimum", 0)
    if schema_type == "boolean":
        return True
    if schema.get("format") == "date-time":
        return "2030-01-01T00:00:00Z"
    if schema.get("pattern"):
        if field == "locale":
            return "en-US"
        return "sha256:" + "a" * 64
    if schema.get("format") == "uri":
        return "https://buyer-ops.example/artifacts/test"
    if schema_type == "string" or not schema:
        if field == "value":
            return "a" * 64
        return (
            None if not schema else ("value" if field not in {"id", "tenantId"} else f"{field}-1")
        )
    raise ValueError(f"unsupported schema fragment for {field}: {schema}")


def main() -> None:
    schema = json.loads(SCHEMA_PATH.read_text())
    valid: dict[str, Any] = {}
    invalid: dict[str, Any] = {}
    for selection in schema["oneOf"]:
        name = selection["$ref"].rsplit("/", 1)[-1]
        definition = schema["$defs"][name]
        record = generate(definition, schema)
        valid[name] = record
        distinguishing = next(
            field
            for part in definition["allOf"]
            for field in part.get("required", [])
            if field != "recordType"
        )
        broken = copy.deepcopy(record)
        del broken[distinguishing]
        invalid[name] = {"removedField": distinguishing, "record": broken}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "ontology_0_3_valid.json").write_text(json.dumps(valid, indent=2) + "\n")
    (OUTPUT / "ontology_0_3_invalid.json").write_text(json.dumps(invalid, indent=2) + "\n")


if __name__ == "__main__":
    main()
