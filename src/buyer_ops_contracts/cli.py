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
    admit = sub.add_parser("admit", help="admit one ontology record into canonical PostgreSQL")
    admit.add_argument("record", type=Path)
    sub.add_parser("serve", help="run the HTTP control plane")
    sub.add_parser("worker", help="run the Temporal worker")
    args = parser.parse_args()
    if args.command == "admit":
        from .canonical_repository import CanonicalRepository
        from .control_plane import connect

        dsn = os.environ.get("BUYER_OPS_DATABASE_DSN") or os.environ.get("DATABASE_URL")
        if not dsn:
            raise SystemExit("BUYER_OPS_DATABASE_DSN or DATABASE_URL is required")
        record = json.loads(args.record.read_text())
        tenant_id = str(record.get("tenantId") or "")
        if not tenant_id:
            raise SystemExit("record tenantId is required")
        connection = connect(dsn)
        try:
            saved = CanonicalRepository(connection, tenant_id=tenant_id).save(record)
        finally:
            connection.close()
        print(
            json.dumps(
                {"id": saved["id"], "recordType": saved["recordType"], "version": saved["version"]}
            )
        )
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


if __name__ == "__main__":
    raise SystemExit(main())
