import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "buyer_ops_contracts"


def _generate(schema: Path, output: Path) -> None:
    subprocess.run(
        [
            str(Path(sys.executable).with_name("datamodel-codegen")),
            "--input",
            str(schema),
            "--input-file-type",
            "jsonschema",
            "--output",
            str(output),
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.12",
            "--use-standard-collections",
            "--use-union-operator",
            "--strict-nullable",
            "--use-subclass-enum",
            "--disable-timestamp",
        ],
        check=True,
    )


def main() -> None:
    manifest = json.loads((PACKAGE / "contracts.manifest.json").read_text())
    sources = {
        "gateway": ROOT / "COGNITIVE-RUNTIME-GATEWAY.schema.json",
        "ontology": ROOT / "ONTOLOGY-V0.schema.json",
    }
    for entry in manifest["contracts"]:
        packaged = PACKAGE / entry["resource"]
        source = sources[entry["name"]]
        if packaged.read_bytes() != source.read_bytes():
            raise SystemExit(f"packaged schema drift: {entry['name']}")
        if hashlib.sha256(packaged.read_bytes()).hexdigest() != entry["sha256"]:
            raise SystemExit(f"manifest digest drift: {entry['name']}")
        schema = json.loads(packaged.read_text())
        Draft202012Validator.check_schema(schema)
        if schema["$id"] != entry["schemaId"]:
            raise SystemExit(f"schema identity drift: {entry['name']}")
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        for name, source in sources.items():
            generated = temporary / f"{name}.py"
            _generate(source, generated)
            committed = PACKAGE / "generated" / f"{name}.py"
            if generated.read_bytes() != committed.read_bytes():
                raise SystemExit(f"generated model drift: {name}")
    print("contract sources, identities, hashes, and generated models are synchronized")


if __name__ == "__main__":
    main()

