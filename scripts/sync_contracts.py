import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "buyer_ops_contracts"
MAPPINGS = {
    "authority_activation_fair_housing": (
        ROOT / "OPEN-025-027.schema.json",
        PACKAGE / "schemas/authority-activation-fair-housing.schema.json",
    ),
    "closure": (
        ROOT / "OPEN-019-024.schema.json",
        PACKAGE / "schemas/closure.schema.json",
    ),
    "gateway": (
        ROOT / "COGNITIVE-RUNTIME-GATEWAY.schema.json",
        PACKAGE / "schemas/gateway.schema.json",
    ),
    "gateway_runtime": (
        ROOT / "GATEWAY-RUNTIME-CONFIG.schema.json",
        PACKAGE / "schemas/gateway_runtime.schema.json",
    ),
    "ontology": (ROOT / "ONTOLOGY-V0.schema.json", PACKAGE / "schemas/ontology.schema.json"),
    "habitat": (ROOT / "HABITAT-EFFECT.schema.json", PACKAGE / "schemas/habitat.schema.json"),
    "temporal": (
        ROOT / "TEMPORAL-WORKFLOW.schema.json",
        PACKAGE / "schemas/temporal.schema.json",
    ),
    "context": (
        ROOT / "CONTEXT-COMPILER.schema.json",
        PACKAGE / "schemas/context.schema.json",
    ),
    "operator_surface": (
        ROOT / "OPERATOR-SURFACE.schema.json",
        PACKAGE / "schemas/operator_surface.schema.json",
    ),
    "telemetry_slo": (
        ROOT / "TELEMETRY-SLO.schema.json",
        PACKAGE / "schemas/telemetry_slo.schema.json",
    ),
    "ot01_ingress": (
        ROOT / "OT01-INGRESS.schema.json",
        PACKAGE / "schemas/ot01_ingress.schema.json",
    ),
    "connector_gateway": (
        ROOT / "CONNECTOR-GATEWAY.schema.json",
        PACKAGE / "schemas/connector_gateway.schema.json",
    ),
    "release_activation": (
        ROOT / "RELEASE-ACTIVATION.schema.json",
        PACKAGE / "schemas/release_activation.schema.json",
    ),
}


def main() -> None:
    manifest_path = PACKAGE / "contracts.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    entries = {entry["name"]: entry for entry in manifest["contracts"]}
    for name, (source, target) in MAPPINGS.items():
        shutil.copyfile(source, target)
        entries.setdefault(name, {"name": name, "resource": f"schemas/{name}.schema.json"})
        entries[name]["resource"] = f"schemas/{target.name}"
        entries[name]["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        entries[name]["schemaId"] = json.loads(target.read_text())["$id"]
        entries[name]["schemaVersion"] = entries[name]["schemaId"].rsplit("/", 1)[-1]
        if name == "ontology":
            entries[name]["readerRange"] = ">=0.3.0,<0.4.0"
            entries[name]["writerVersion"] = entries[name]["schemaVersion"]
        elif name in {"closure", "context", "operator_surface", "ot01_ingress"}:
            entries[name]["readerRange"] = ">=1.1.0,<2.0.0"
            entries[name]["writerVersion"] = entries[name]["schemaVersion"]
        else:
            entries[name]["readerRange"] = (
                f">={entries[name]['schemaVersion'].split('.')[0]}.0.0,<{int(entries[name]['schemaVersion'].split('.')[0]) + 1}.0.0"
            )
            entries[name]["writerVersion"] = entries[name]["schemaVersion"]
    manifest["contracts"] = [entries[name] for name in MAPPINGS]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
