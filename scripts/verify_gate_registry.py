from __future__ import annotations

import sys
from pathlib import Path

import yaml

from buyer_ops_contracts.gate_registry import validate_registry

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "PRODUCTION-GATE-REGISTRY.yaml"


def main() -> None:
    try:
        loaded = yaml.safe_load(REGISTRY_PATH.read_text())
        if not isinstance(loaded, dict):
            raise ValueError("gate registry must contain a mapping at the document root")
        errors = validate_registry(loaded)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"gate registry validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if errors:
        for error in errors:
            print(f"gate registry validation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("gate registry shape, ordering, dependencies, and activation metadata are valid")


if __name__ == "__main__":
    main()
