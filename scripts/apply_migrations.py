"""Apply ordered forward migrations exactly once to the configured PostgreSQL database."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, cast

import psycopg

from buyer_ops_contracts.actor_authorization import admit_published_record
from buyer_ops_contracts.canonical_repository import Connection
from buyer_ops_contracts.closure_repository import PostgresClosureRepository

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = sorted(
    migration
    for migration in (ROOT / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql")
    if not migration.name.endswith(".rollback.sql")
)


def main() -> int:
    dsn = os.environ.get("BUYER_OPS_DATABASE_DSN") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("BUYER_OPS_DATABASE_DSN or DATABASE_URL is required")
    if not MIGRATIONS:
        raise SystemExit("no forward migrations found")

    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_lock(hashtextextended(%s, 0))", ("buyer-ops-migrations",)
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS buyer_ops_schema_migrations (
                    version text PRIMARY KEY,
                    checksum text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
                )
                """
            )
            for migration in MIGRATIONS:
                version = migration.name.split("_", 1)[0]
                sql = migration.read_text()
                checksum = "sha256:" + hashlib.sha256(sql.encode()).hexdigest()
                cursor.execute(
                    "SELECT checksum FROM buyer_ops_schema_migrations WHERE version = %s",
                    (version,),
                )
                row = cursor.fetchone()
                if row is not None:
                    if row[0] != checksum:
                        raise RuntimeError(
                            f"migration checksum changed after application: {version}"
                        )
                    continue
                cursor.execute(sql)
                cursor.execute(
                    "INSERT INTO buyer_ops_schema_migrations (version, checksum) VALUES (%s, %s)",
                    (version, checksum),
                )
                print(f"applied {migration.name}")
            cursor.execute(
                "SELECT pg_advisory_unlock(hashtextextended(%s, 0))", ("buyer-ops-migrations",)
            )
        _apply_bootstrap_records(connection)
    return 0


def _apply_bootstrap_records(connection: psycopg.Connection) -> None:
    encoded = os.environ.get("BUYER_OPS_BOOTSTRAP_RECORDS_JSON", "").strip()
    if not encoded:
        raise SystemExit("BUYER_OPS_BOOTSTRAP_RECORDS_JSON is required")
    records = json.loads(encoded)
    if not isinstance(records, list) or not records:
        raise ValueError("BUYER_OPS_BOOTSTRAP_RECORDS_JSON must be a non-empty array")
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("bootstrap records must be objects")
        record_type = record.get("recordType") or record.get("message_type")
        if record_type not in {
            "ActorTenantAuthorization",
            "operator_policy",
            "LicenseHolder",
            "Person",
        }:
            raise ValueError(
                "bootstrap permits only ActorTenantAuthorization, operator_policy, Person, and LicenseHolder"
            )
        current = _bootstrap_current(connection, record)
        if current is not None:
            if current != record:
                raise RuntimeError(
                    f"bootstrap record conflicts with current {record_type}: "
                    f"{record.get('recordId') or record.get('policy_id')}"
                )
            continue
        admit_published_record(cast(Connection, connection), record)
        print(f"bootstrapped {record_type}:{record.get('recordId') or record.get('policy_id')}")
    _apply_bootstrap_closure_records(connection)


def _apply_bootstrap_closure_records(connection: psycopg.Connection) -> None:
    encoded = os.environ.get("BUYER_OPS_BOOTSTRAP_CLOSURE_RECORDS_JSON", "").strip()
    if not encoded:
        return
    records = json.loads(encoded)
    if not isinstance(records, list):
        raise ValueError("BUYER_OPS_BOOTSTRAP_CLOSURE_RECORDS_JSON must be an array")
    for record in records:
        if not isinstance(record, dict) or record.get("recordType") != "EffectPolicy":
            raise ValueError("bootstrap closure records currently permit only EffectPolicy")
        repository = PostgresClosureRepository(
            cast(Connection, connection), tenant_id=str(record.get("tenantId") or "")
        )
        identity_key = json.dumps([record["policyId"]], separators=(",", ":"))
        current = repository.current("EffectPolicy", identity_key)
        if current is None:
            repository.save(record)
        elif current != record:
            raise RuntimeError(f"bootstrap closure conflicts with current {record['recordId']}")
        else:
            continue
        print(f"bootstrapped closure {record['recordType']}:{record['recordId']}")


def _bootstrap_current(
    connection: psycopg.Connection, record: dict[str, object]
) -> dict[str, object] | None:
    if record.get("recordType") == "ActorTenantAuthorization":
        query = """
            SELECT payload FROM actor_tenant_authorizations_current
            WHERE tenant_id = %s AND record_id = %s
        """
        parameters = (record["tenantId"], record["recordId"])
    elif record.get("message_type") == "operator_policy":
        query = """
            SELECT policy FROM operator_policies_current
            WHERE tenant_id = %s AND policy_id = %s
        """
        parameters = (record["tenant_id"], record["policy_id"])
    else:
        query = """
            SELECT record FROM canonical_records_current
            WHERE tenant_id = %s AND record_id = %s
        """
        parameters = (record["tenantId"], record["id"])
    with connection.cursor() as cursor:
        cursor.execute(query, parameters)
        row = cursor.fetchone()
    if row is None:
        return None
    value = row[0]
    if isinstance(value, str):
        value = json.loads(value)
    return cast(dict[str, Any], value)


if __name__ == "__main__":
    raise SystemExit(main())
