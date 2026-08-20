import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "sync_contracts", ROOT / "scripts/sync_contracts.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree(tmp_path: Path, module: ModuleType) -> tuple[Path, Path, Path]:
    root = tmp_path / "root"
    package = root / "src" / "buyer_ops_contracts"
    (package / "schemas").mkdir(parents=True)
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://buyer-ops.example/schemas/example/1.0.0",
        "type": "object",
    }
    source = root / "EXAMPLE.schema.json"
    source.write_text(json.dumps(schema) + "\n")
    target = package / "schemas" / "example.schema.json"
    target.write_bytes(source.read_bytes())
    module.ROOT = root
    module.PACKAGE = package
    module.MAPPINGS = {"example": ("EXAMPLE.schema.json", "example.schema.json")}
    catalog_source = root / "TELEMETRY-SLO-CATALOG.json"
    catalog_source.write_text('{"catalogVersion":"telemetry-slo-catalog/1.0.0"}\n')
    module.TELEMETRY_CATALOG = (
        "TELEMETRY-SLO-CATALOG.json",
        "telemetry_catalog.json",
    )
    manifest = {"manifestVersion": "1.1.0", "contracts": []}
    (package / "contracts.manifest.json").write_text(json.dumps(manifest) + "\n")
    module.write()
    return source, target, package / "telemetry_catalog.json"


def test_check_is_byte_for_byte_read_only(tmp_path: Path) -> None:
    module = _module()
    source, target, catalog = _tree(tmp_path, module)
    manifest = module.PACKAGE / "contracts.manifest.json"
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (source, target, catalog, manifest)
    }
    module.check()
    after = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (source, target, catalog, manifest)
    }
    assert after == before


def test_check_reports_drift_without_repairing_it(tmp_path: Path) -> None:
    module = _module()
    _, target, _ = _tree(tmp_path, module)
    target.write_text("{}\n")
    before = target.read_bytes()
    with pytest.raises(SystemExit, match="packaged schema drift"):
        module.check()
    assert target.read_bytes() == before


def test_check_reports_packaged_catalog_drift_without_repairing_it(tmp_path: Path) -> None:
    module = _module()
    _, _, catalog = _tree(tmp_path, module)
    catalog.write_text("{}\n")
    before = catalog.read_bytes()

    with pytest.raises(SystemExit, match="packaged telemetry catalog drift"):
        module.check()

    assert catalog.read_bytes() == before
