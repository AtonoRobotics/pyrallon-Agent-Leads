from __future__ import annotations

import sys
from pathlib import Path

import yaml

from buyer_ops_contracts.completion_ledger import validate_completion_ledger

ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "OT01-COMPLETION-LEDGER.yaml"


def main() -> None:
    try:
        loaded = yaml.safe_load(LEDGER_PATH.read_text())
        if not isinstance(loaded, dict):
            raise ValueError("completion ledger must contain a mapping at the document root")
        errors = validate_completion_ledger(loaded, ROOT)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"completion ledger validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if errors:
        for error in errors:
            print(f"completion ledger validation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("OT-01 completion ledger covers sections 1-19 and makes no false activation claim")


if __name__ == "__main__":
    main()
