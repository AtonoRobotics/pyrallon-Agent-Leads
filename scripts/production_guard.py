"""Run the non-negotiable whole-repository production execution gates."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail closed unless whole-repository production scope and execution are valid."
    )
    parser.add_argument(
        "--release",
        action="store_true",
        help="also require the complete ledger and execute all release verification commands",
    )
    args = parser.parse_args()
    commands = [
        [sys.executable, "scripts/verify_production_scope.py"],
        [sys.executable, "scripts/verify_production_execution.py"],
        [sys.executable, "scripts/verify_production_ledger.py", "--check"],
    ]
    if args.release:
        commands[-1] = [sys.executable, "scripts/verify_production_ledger.py", "--release"]
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            print("production guard rejected the repository state", file=sys.stderr)
            return result.returncode
    print("whole-repository production guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
