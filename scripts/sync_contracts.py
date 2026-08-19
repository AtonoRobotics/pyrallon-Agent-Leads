import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "buyer_ops_contracts"
MAPPINGS = {
    "closure": (ROOT / "OPEN-019-024.schema.json", PACKAGE / "schemas/closure.schema.json"),
    "gateway": (
        ROOT / "COGNITIVE-RUNTIME-GATEWAY.schema.json",
        PACKAGE / "schemas/gateway.schema.json",
    ),
    "ontology": (ROOT / "ONTOLOGY-V0.schema.json", PACKAGE / "schemas/ontology.schema.json"),
}


def main() -> None:
    manifest_path = PACKAGE / "contracts.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    entries = {entry["name"]: entry for entry in manifest["contracts"]}
    for name, (source, target) in MAPPINGS.items():
        shutil.copyfile(source, target)
        entries[name]["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        entries[name]["schemaId"] = json.loads(target.read_text())["$id"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()

