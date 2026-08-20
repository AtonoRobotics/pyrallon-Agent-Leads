from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest

from buyer_ops_contracts.derived_contract_repository import DerivedContractReader

ROOT = Path(__file__).resolve().parents[1]


class _Cursor:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self._row = row

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        return None

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row

    def fetchall(self) -> list[tuple[object, ...]]:
        return []


class _Connection:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self._row = row
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _Cursor:
        return _Cursor(self._row)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _qualification_policy() -> dict[str, Any]:
    fixture = json.loads((ROOT / "tests/fixtures/qualification_readiness/valid.json").read_text())
    return cast(dict[str, Any], fixture["policy"])


def test_reader_rejects_a_structurally_valid_payload_with_mismatched_identity() -> None:
    payload = _qualification_policy()
    connection = _Connection(
        (
            "qualification_readiness",
            "qualification_policy",
            "different-policy",
            1,
            payload["schemaVersion"],
            payload,
        )
    )

    @contextmanager
    def connection_factory() -> Iterator[_Connection]:
        yield connection

    with pytest.raises(RuntimeError, match="envelope mismatch"):
        DerivedContractReader(connection_factory, tenant_id="tenant-a").get(
            contract_family="qualification_readiness",
            message_type="qualification_policy",
            record_id="different-policy",
            record_version=1,
        )
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_reader_rejects_an_unpublished_contract_before_opening_a_connection() -> None:
    opened = False

    @contextmanager
    def connection_factory() -> Iterator[_Connection]:
        nonlocal opened
        opened = True
        yield _Connection(None)

    with pytest.raises(KeyError, match="unsupported contract"):
        DerivedContractReader(connection_factory, tenant_id="tenant-a").get(
            contract_family="unpublished_family",
            message_type="qualification_policy",
            record_id="qualification-policy-a",
            record_version=1,
        )
    assert not opened
