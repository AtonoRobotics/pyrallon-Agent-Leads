from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

import rfc8785

from buyer_ops_contracts.acknowledgment import normalize_opt_out_text
from buyer_ops_contracts.acknowledgment_repository import AcknowledgmentRepository
from buyer_ops_contracts.canonical_repository import Connection, Cursor

ROOT = Path(__file__).resolve().parents[1]


class _ReplayCursor:
    def __init__(self, replay: tuple[object, ...]) -> None:
        self.replay = replay
        self.executions: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> Cursor:
        return cast(Cursor, self)

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        self.executions.append((statement, parameters))

    def fetchone(self) -> tuple[object, ...] | None:
        return self.replay

    def fetchall(self) -> list[tuple[object, ...]]:
        return []


class _ReplayConnection:
    def __init__(self, replay: tuple[object, ...]) -> None:
        self.cursor_instance = _ReplayCursor(replay)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> Cursor:
        return cast(Cursor, self.cursor_instance)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_decide_locks_idempotency_key_before_replay_lookup() -> None:
    fixtures = json.loads((ROOT / "tests/fixtures/closure/ot01_ingress_valid.json").read_text())
    request = fixtures["AcknowledgmentDecisionRequest"]
    raw_text = " STOP "
    digest = (
        "sha256:"
        + hashlib.sha256(rfc8785.dumps([request, normalize_opt_out_text(raw_text)])).hexdigest()
    )
    stored = {"decisionId": request["decisionId"], "disposition": "do_not_send"}
    connection = _ReplayConnection((digest, stored))

    result = AcknowledgmentRepository(
        cast(Connection, connection), tenant_id=request["tenantId"]
    ).decide(request, raw_text, b"unused-on-replay")

    assert result == stored
    statements = connection.cursor_instance.executions
    assert "set_config" in statements[0][0]
    assert "pg_advisory_xact_lock" in statements[1][0]
    assert statements[1][1] == (
        f"acknowledgment-decision:{request['tenantId']}:{request['idempotencyKey']}",
    )
    assert "ingress_acknowledgment_decisions" in statements[2][0]
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_admit_config_locks_stable_identity_before_current_version_lookup() -> None:
    fixtures = json.loads((ROOT / "tests/fixtures/closure/ot01_ingress_valid.json").read_text())
    policy = fixtures["AcknowledgmentPolicy"]
    connection = _ReplayConnection(None)

    AcknowledgmentRepository(
        cast(Connection, connection), tenant_id=policy["tenantId"]
    ).admit_config(policy)

    statements = connection.cursor_instance.executions
    assert "set_config" in statements[0][0]
    assert "pg_advisory_xact_lock" in statements[1][0]
    assert statements[1][1] == (
        f"acknowledgment-config:{policy['tenantId']}:{policy['messageType']}:{policy['policyId']}",
    )
    assert "ingress_ack_configs_current" in statements[2][0]
    assert connection.commits == 1
    assert connection.rollbacks == 0
