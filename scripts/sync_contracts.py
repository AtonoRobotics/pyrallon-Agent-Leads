"""Synchronize root contract schemas into the installable package.

The default and ``--check`` modes are strictly read-only.  The explicit
``--write`` mode is the only code path allowed to mutate package resources or
the manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "buyer_ops_contracts"
TELEMETRY_CATALOG = ("TELEMETRY-SLO-CATALOG.json", "telemetry_catalog.json")
MAPPINGS = {
    "authority_activation_fair_housing": (
        "OPEN-025-027.schema.json",
        "authority-activation-fair-housing.schema.json",
    ),
    "closure": ("OPEN-019-024.schema.json", "closure.schema.json"),
    "gateway": ("COGNITIVE-RUNTIME-GATEWAY.schema.json", "gateway.schema.json"),
    "gateway_runtime": (
        "GATEWAY-RUNTIME-CONFIG.schema.json",
        "gateway_runtime.schema.json",
    ),
    "ontology": ("ONTOLOGY-V0.schema.json", "ontology.schema.json"),
    "habitat": ("HABITAT-EFFECT.schema.json", "habitat.schema.json"),
    "temporal": ("TEMPORAL-WORKFLOW.schema.json", "temporal.schema.json"),
    "context": ("CONTEXT-COMPILER.schema.json", "context.schema.json"),
    "operator_surface": (
        "OPERATOR-SURFACE.schema.json",
        "operator_surface.schema.json",
    ),
    "telemetry_slo": ("TELEMETRY-SLO.schema.json", "telemetry_slo.schema.json"),
    "ot01_ingress": ("OT01-INGRESS.schema.json", "ot01_ingress.schema.json"),
    "connector_gateway": (
        "CONNECTOR-GATEWAY.schema.json",
        "connector_gateway.schema.json",
    ),
    "release_activation": (
        "RELEASE-ACTIVATION.schema.json",
        "release_activation.schema.json",
    ),
    "qualification_readiness": (
        "QUALIFICATION-READINESS.schema.json",
        "qualification_readiness.schema.json",
    ),
    "availability_booking": (
        "AVAILABILITY-BOOKING.schema.json",
        "availability_booking.schema.json",
    ),
}


def _reader_range(name: str, version: str) -> str:
    if name == "ontology":
        return ">=0.3.0,<0.4.0"
    if name in {"closure", "context", "operator_surface", "ot01_ingress"}:
        return ">=1.1.0,<2.0.0"
    major = int(version.split(".", 1)[0])
    return f">={major}.0.0,<{major + 1}.0.0"


def _expected_entry(name: str, target_name: str, source: Path) -> dict[str, Any]:
    schema = json.loads(source.read_text())
    version = schema["$id"].rsplit("/", 1)[-1]
    return {
        "name": name,
        "resource": f"schemas/{target_name}",
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "schemaId": schema["$id"],
        "schemaVersion": version,
        "readerRange": _reader_range(name, version),
        "writerVersion": version,
    }


def expected_manifest() -> dict[str, Any]:
    current = json.loads((PACKAGE / "contracts.manifest.json").read_text())
    contracts = []
    for name, (source_name, target_name) in MAPPINGS.items():
        contracts.append(_expected_entry(name, target_name, ROOT / source_name))
    return {"manifestVersion": current["manifestVersion"], "contracts": contracts}


def check() -> None:
    errors: list[str] = []
    for name, (source_name, target_name) in MAPPINGS.items():
        source = ROOT / source_name
        target = PACKAGE / "schemas" / target_name
        if not target.is_file() or source.read_bytes() != target.read_bytes():
            errors.append(f"packaged schema drift: {name}")
    catalog_source = ROOT / TELEMETRY_CATALOG[0]
    catalog_target = PACKAGE / TELEMETRY_CATALOG[1]
    if not catalog_target.is_file() or catalog_source.read_bytes() != catalog_target.read_bytes():
        errors.append("packaged telemetry catalog drift")
    actual = json.loads((PACKAGE / "contracts.manifest.json").read_text())
    expected = expected_manifest()
    if actual != expected:
        errors.append("contract manifest drift")
    if errors:
        raise SystemExit("\n".join(errors))
    print("contract sources, packaged schemas, and manifest are synchronized")


def write() -> None:
    for source_name, target_name in MAPPINGS.values():
        shutil.copyfile(ROOT / source_name, PACKAGE / "schemas" / target_name)
    shutil.copyfile(ROOT / TELEMETRY_CATALOG[0], PACKAGE / TELEMETRY_CATALOG[1])
    manifest = expected_manifest()
    (PACKAGE / "contracts.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("contract package synchronized")


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="verify without writing (default)")
    mode.add_argument("--write", action="store_true", help="update packaged schemas and manifest")
    args = parser.parse_args()
    if args.write:
        write()
    else:
        check()


if __name__ == "__main__":
    main()
