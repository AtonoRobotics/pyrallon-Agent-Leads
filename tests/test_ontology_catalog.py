import importlib.util
import json
from pathlib import Path

import pytest

from buyer_ops_contracts.errors import ContractViolation
from buyer_ops_contracts.structural import validate_record

FIXTURES = Path(__file__).parent / "fixtures" / "generated"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


def test_every_catalog_type_has_valid_and_invalid_golden_fixture() -> None:
    valid = _load("ontology_0_3_valid.json")
    invalid = _load("ontology_0_3_invalid.json")
    assert set(valid) == set(invalid)
    assert len(valid) == 40
    for record_type, record in valid.items():
        assert record["recordType"] == record_type
        validate_record(record, "ontology")
    for case in invalid.values():
        assert case["removedField"] not in case["record"]
        with pytest.raises(ContractViolation):
            validate_record(case["record"], "ontology")


def test_catalog_fixture_generation_is_drift_free(tmp_path, monkeypatch) -> None:
    script = Path(__file__).parents[1] / "scripts" / "generate_ontology_fixtures.py"
    specification = importlib.util.spec_from_file_location("generate_ontology_fixtures", script)
    assert specification and specification.loader
    generate_ontology_fixtures = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(generate_ontology_fixtures)

    monkeypatch.setattr(generate_ontology_fixtures, "OUTPUT", tmp_path)
    generate_ontology_fixtures.main()
    for name in ("ontology_0_3_valid.json", "ontology_0_3_invalid.json"):
        assert (tmp_path / name).read_bytes() == (FIXTURES / name).read_bytes()


def test_ontology_0_2_compatibility_report_is_complete() -> None:
    report = json.loads(
        (Path(__file__).parents[1] / "ONTOLOGY-0.1-TO-0.2-COMPATIBILITY.json").read_text()
    )
    assert report["classification"] == "minor_with_explicit_migration"
    assert report["rootRecordTypeCount"] == {"previous": 15, "current": 38}
    assert len(report["addedRecordTypes"]) == 24
    assert report["removedRootShapes"] == ["EpistemicItem"]
    assert report["compatibilityRules"]["legacyEpistemicItem"].startswith("fail-closed")


def test_pkt02_packaged_manifest_targets_ontology_0_3() -> None:
    manifest = json.loads(
        (
            Path(__file__).parents[1] / "src" / "buyer_ops_contracts" / "contracts.manifest.json"
        ).read_text()
    )
    ontology = next(item for item in manifest["contracts"] if item["name"] == "ontology")
    assert ontology["schemaVersion"] == "0.3.0"
    assert ontology["writerVersion"] == "0.3.0"
    assert ontology["readerRange"] == ">=0.3.0,<0.4.0"
