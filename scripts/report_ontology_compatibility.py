"""Emit the governed 0.1-to-0.2 compatibility and migration report."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "contracts" / "ontology-0.1-baseline.json"
SCHEMA = ROOT / "ONTOLOGY-V0.schema.json"
OUTPUT = ROOT / "ONTOLOGY-0.1-TO-0.2-COMPATIBILITY.json"


def main() -> None:
    baseline = json.loads(BASELINE.read_text())
    schema = json.loads(SCHEMA.read_text())
    current = [selection["$ref"].rsplit("/", 1)[-1] for selection in schema["oneOf"]]
    previous = baseline["rootRecordTypes"]
    added = sorted(set(current) - set(previous))
    removed = sorted(set(previous) - set(current))
    report = {
        "previousVersion": baseline["schemaVersion"],
        "currentVersion": "buyer-ops/" + schema["$id"].rsplit("/", 1)[-1],
        "classification": "minor_with_explicit_migration",
        "rootRecordTypeCount": {"previous": len(previous), "current": len(current)},
        "addedRecordTypes": added,
        "removedRootShapes": removed,
        "declaredDefaults": baseline["migrationRequiredFields"],
        "compatibilityRules": {
            "legacyPersonEndpoints": "accepted as deprecated input during 0.2 backfill",
            "legacyEnvelopeStatus": "normalized to the universal lifecycle",
            "legacyEpistemicItem": "fail-closed typed forward repair required",
            "storedRows": "envelope schemaVersion is migrated atomically",
            "rollback": "automatic semantic rollback prohibited",
        },
        "migration": "migrations/0004_ontology_0_2.sql",
        "forwardRepair": "migrations/ONTOLOGY-0.2-FORWARD-REPAIR.md",
    }
    if len(current) != 38 or len(added) != 24 or removed != ["EpistemicItem"]:
        raise SystemExit("unexpected ontology compatibility surface")
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
