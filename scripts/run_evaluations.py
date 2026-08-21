"""Run a production evaluation suite against candidate JSON outputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from buyer_ops_contracts.evaluations import EvaluationSuite


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, default=Path("evaluations/production-suite.json"))
    parser.add_argument(
        "--candidates", type=Path, required=True, help="JSON object keyed by caseId"
    )
    args = parser.parse_args()
    candidates = json.loads(args.candidates.read_text())
    if not isinstance(candidates, dict):
        print("candidate file must contain an object keyed by caseId", file=sys.stderr)
        return 1
    suite = EvaluationSuite.from_path(args.suite)
    report = suite.run(lambda case: candidates.get(case.case_id, {}))
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
