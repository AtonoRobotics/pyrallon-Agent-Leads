import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).resolve().parents[1]
FAMILIES = {
    "operator_surface": "OPERATOR-SURFACE.schema.json",
    "telemetry_slo": "TELEMETRY-SLO.schema.json",
    "ot01_ingress": "OT01-INGRESS.schema.json",
    "connector_gateway": "CONNECTOR-GATEWAY.schema.json",
    "release_activation": "RELEASE-ACTIVATION.schema.json",
    "gateway_runtime": "GATEWAY-RUNTIME-CONFIG.schema.json",
    "temporal": "TEMPORAL-WORKFLOW.schema.json",
}


@pytest.mark.parametrize(("family", "schema_name"), FAMILIES.items())
def test_every_closure_message_has_valid_and_invalid_fixture(family: str, schema_name: str) -> None:
    schema = json.loads((ROOT / schema_name).read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    fixture_root = ROOT / "tests" / "fixtures" / "closure"
    valid = json.loads((fixture_root / f"{family}_valid.json").read_text())
    invalid = json.loads((fixture_root / f"{family}_invalid.json").read_text())
    expected = {item["$ref"].rsplit("/", 1)[-1] for item in schema["oneOf"]}
    assert set(valid) == expected == set(invalid)
    for name in expected:
        validator.validate(valid[name])
        with pytest.raises(ValidationError):
            validator.validate(invalid[name]["record"])


def test_telemetry_catalog_is_closed_and_complete() -> None:
    catalog = json.loads((ROOT / "TELEMETRY-SLO-CATALOG.json").read_text())
    ids = {metric["id"] for metric in catalog["metrics"]}
    assert len(ids) == 15
    assert catalog["dimensionPolicy"]["prohibited"] == [
        "tenant_id",
        "person_id",
        "journey_id",
        "message_id",
        "free_text",
    ]
    assert all(slo["noData"] == "insufficient_data" for slo in catalog["slos"])


def test_operator_accessibility_acceptance_is_w3c_level_aa() -> None:
    valid = json.loads((ROOT / "tests/fixtures/closure/operator_surface_valid.json").read_text())[
        "AccessibilityAcceptance"
    ]
    assert valid["standard"] == "WCAG 2.2"
    assert valid["level"] == "AA"
    assert valid["blocking_violations"] == 0


def test_closure_compatibility_covers_exact_packaged_manifest() -> None:
    compatibility = json.loads((ROOT / "SCP-01-COMPATIBILITY.json").read_text())
    manifest = json.loads((ROOT / "src/buyer_ops_contracts/contracts.manifest.json").read_text())
    declared = {item["name"]: item for item in compatibility["contracts"]}
    for report_name in (
        "QUALIFICATION-READINESS-COMPATIBILITY.json",
        "AVAILABILITY-BOOKING-COMPATIBILITY.json",
    ):
        report = json.loads((ROOT / report_name).read_text())
        declared[report["contractFamily"]] = {
            "current": report["schemaVersion"],
            "readerRange": report["readerRange"],
        }
    packaged = {item["name"]: item for item in manifest["contracts"]}
    assert set(declared) == set(packaged)
    for name, entry in packaged.items():
        assert declared[name]["current"] == entry["schemaVersion"]
        assert declared[name]["readerRange"] == entry["readerRange"]
