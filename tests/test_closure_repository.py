from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

from buyer_ops_contracts.canonical_repository import Connection, Cursor
from buyer_ops_contracts.closure_repository import (
    PostgresClosureRepository,
    closure_identity_key,
)

ROOT = Path(__file__).resolve().parents[1]


class _Cursor:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> Cursor:
        return cast(Cursor, self)

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        self.executions.append((statement, parameters))

    def fetchone(self) -> tuple[object, ...] | None:
        return None

    def fetchall(self) -> list[tuple[object, ...]]:
        return []


class _Connection:
    def __init__(self) -> None:
        self.cursor_instance = _Cursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> Cursor:
        return cast(Cursor, self.cursor_instance)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_save_locks_semantic_identity_before_current_version_lookup() -> None:
    fixtures = json.loads((ROOT / "tests/fixtures/generated/closure_1_1_valid.json").read_text())
    record = copy.deepcopy(fixtures["CapabilityInventory"])
    connection = _Connection()

    PostgresClosureRepository(cast(Connection, connection), tenant_id=record["tenantId"]).save(
        record
    )

    statements = connection.cursor_instance.executions
    assert "set_config" in statements[0][0]
    assert "pg_advisory_xact_lock" in statements[1][0]
    assert statements[1][1] == (
        f"closure:{record['tenantId']}:{record['recordType']}:{closure_identity_key(record)}",
    )
    assert "closure_records_current" in statements[2][0]
    assert connection.commits == 1
    assert connection.rollbacks == 0
