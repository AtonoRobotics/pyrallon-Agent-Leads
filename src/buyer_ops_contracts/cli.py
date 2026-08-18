import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .errors import ContractViolation
from .semantic import validate_semantics
from .structural import validate_record


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Buyer Operations contract record")
    parser.add_argument("contract", choices=("gateway", "ontology"))
    parser.add_argument("record", type=Path)
    args = parser.parse_args()
    record = json.loads(args.record.read_text())
    try:
        validate_record(record, args.contract)
        validate_semantics(record)
    except ContractViolation as exc:
        print(json.dumps([asdict(v) for v in exc.violations], indent=2))
        return 1
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

