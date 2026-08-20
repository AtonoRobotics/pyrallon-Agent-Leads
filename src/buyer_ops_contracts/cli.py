import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from .errors import ContractViolation
from .semantic import validate_semantics
from .structural import validate_record


def main() -> int:
    parser = argparse.ArgumentParser(description="Buyer Operations contract tools")
    sub = parser.add_subparsers(dest="command")
    validate = sub.add_parser("validate", help="validate one contract record")
    validate.add_argument("contract")
    validate.add_argument("record", type=Path)
    admit = sub.add_parser("admit", help="admit one published record into PostgreSQL")
    admit.add_argument("record", type=Path)
    sub.add_parser("serve", help="run the HTTP control plane")
    sub.add_parser("worker", help="run the Temporal worker")
    args = parser.parse_args()
    if args.command == "admit":
        from .actor_authorization import admit_published_record
        from .control_plane import connect

        dsn = os.environ.get("BUYER_OPS_DATABASE_DSN") or os.environ.get("DATABASE_URL")
        if not dsn:
            raise SystemExit("BUYER_OPS_DATABASE_DSN or DATABASE_URL is required")
        record = json.loads(args.record.read_text())
        connection = connect(dsn)
        try:
            saved = admit_published_record(connection, record)
        finally:
            connection.close()
        print(json.dumps(_admission_summary(saved)))
        return 0
    if args.command == "serve":
        from .control_plane import main as serve_main

        return serve_main()
    if args.command == "worker":
        from .worker_main import main as worker_main

        return worker_main()
    if args.command != "validate":
        parser.print_help()
        return 2
    record = json.loads(args.record.read_text())
    try:
        validate_record(record, args.contract)
        validate_semantics(record)
    except ContractViolation as exc:
        print(json.dumps([asdict(v) for v in exc.violations], indent=2))
        return 1
    print("valid")
    return 0


def _admission_summary(record: dict[str, object]) -> dict[str, object | None]:
    return {
        "id": record.get("id") or record.get("recordId") or record.get("policy_id"),
        "recordType": record.get("recordType") or record.get("message_type"),
        "version": (
            record.get("version")
            or record.get("authorizationVersion")
            or record.get("record_version")
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())
