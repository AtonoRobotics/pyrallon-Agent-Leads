from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest

from buyer_ops_contracts.canonical_repository import TenantIsolationViolation
from buyer_ops_contracts.contract_acceptance import ContractSemanticError
from buyer_ops_contracts.derived_contract_repository import (
    BookingOutcomeRepository,
    DerivedContractReader,
    QualificationDecisionPairRepository,
    SlotSetRepository,
)
from buyer_ops_contracts.errors import ContractViolation

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


class _WriteCursor:
    def __init__(self, *, fail_on_execute: int | None = None) -> None:
        self.executions: list[tuple[str, tuple[object, ...]]] = []
        self._fail_on_execute = fail_on_execute

    def __enter__(self) -> _WriteCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        self.executions.append((statement, parameters))
        if len(self.executions) == self._fail_on_execute:
            raise RuntimeError("injected second decision failure")

    def fetchone(self) -> tuple[object, ...] | None:
        return None

    def fetchall(self) -> list[tuple[object, ...]]:
        return []


class _WriteConnection:
    def __init__(self, *, fail_on_execute: int | None = None) -> None:
        self.cursor_instance = _WriteCursor(fail_on_execute=fail_on_execute)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _WriteCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _qualification_policy() -> dict[str, Any]:
    fixture = json.loads((ROOT / "tests/fixtures/qualification_readiness/valid.json").read_text())
    return cast(dict[str, Any], fixture["policy"])


def _qualification_records() -> tuple[dict[str, Any], ...]:
    fixture = json.loads((ROOT / "tests/fixtures/qualification_readiness/valid.json").read_text())
    return tuple(
        cast(dict[str, Any], fixture[key])
        for key in ("policy", "input", "nextQuestion", "readiness")
    )


def _booking_records() -> tuple[dict[str, Any], ...]:
    fixture = json.loads((ROOT / "tests/fixtures/availability_booking/valid.json").read_text())
    return tuple(
        cast(dict[str, Any], fixture[key])
        for key in ("binding", "command", "result", "reconciliation")
    )


def _slot_set_records() -> tuple[dict[str, Any], ...]:
    availability = json.loads((ROOT / "tests/fixtures/availability_booking/valid.json").read_text())
    qualification = json.loads(
        (ROOT / "tests/fixtures/qualification_readiness/valid.json").read_text()
    )
    readiness = cast(dict[str, Any], qualification["readiness"])
    readiness["expiresAt"] = "2026-03-08T08:05:00Z"
    return tuple(
        cast(dict[str, Any], record)
        for record in (
            availability["policy"],
            readiness,
            availability["binding"],
            availability["snapshot"],
            availability["slotSet"],
        )
    )


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


def test_qualification_decision_pair_is_validated_before_atomic_append() -> None:
    connection = _WriteConnection()

    @contextmanager
    def connection_factory() -> Iterator[_WriteConnection]:
        yield connection

    policy, inputs, next_question, readiness = _qualification_records()
    QualificationDecisionPairRepository(
        connection_factory, tenant_id="tenant-a"
    ).append_decision_pair(
        policy=policy,
        inputs=inputs,
        next_question=next_question,
        readiness=readiness,
    )

    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert len(connection.cursor_instance.executions) == 3
    inserts = connection.cursor_instance.executions[1:]
    assert [parameters[2] for _, parameters in inserts] == [
        "next_question_decision",
        "readiness_decision",
    ]


def test_qualification_decision_pair_rejects_invalid_records_before_opening() -> None:
    opened = False

    @contextmanager
    def connection_factory() -> Iterator[_WriteConnection]:
        nonlocal opened
        opened = True
        yield _WriteConnection()

    policy, inputs, next_question, readiness = _qualification_records()
    readiness["result"] = "not_ready"
    repository = QualificationDecisionPairRepository(connection_factory, tenant_id="tenant-a")
    with pytest.raises(ContractSemanticError, match="readiness_result_mismatch"):
        repository.append_decision_pair(
            policy=policy,
            inputs=inputs,
            next_question=next_question,
            readiness=readiness,
        )
    assert not opened


@pytest.mark.parametrize("failure", ["structural", "tenant"])
def test_qualification_decision_pair_rejects_unadmitted_envelope_before_opening(
    failure: str,
) -> None:
    opened = False

    @contextmanager
    def connection_factory() -> Iterator[_WriteConnection]:
        nonlocal opened
        opened = True
        yield _WriteConnection()

    policy, inputs, next_question, readiness = _qualification_records()
    expected: type[Exception]
    if failure == "structural":
        next_question.pop("reasonCodes")
        expected = ContractViolation
    else:
        readiness["tenantId"] = "tenant-other"
        expected = TenantIsolationViolation
    repository = QualificationDecisionPairRepository(connection_factory, tenant_id="tenant-a")
    with pytest.raises(expected):
        repository.append_decision_pair(
            policy=policy,
            inputs=inputs,
            next_question=next_question,
            readiness=readiness,
        )
    assert not opened


def test_qualification_decision_pair_rolls_back_second_insert_failure() -> None:
    connection = _WriteConnection(fail_on_execute=3)

    @contextmanager
    def connection_factory() -> Iterator[_WriteConnection]:
        yield connection

    policy, inputs, next_question, readiness = _qualification_records()
    repository = QualificationDecisionPairRepository(connection_factory, tenant_id="tenant-a")
    with pytest.raises(RuntimeError, match="second decision failure"):
        repository.append_decision_pair(
            policy=policy,
            inputs=inputs,
            next_question=next_question,
            readiness=readiness,
        )
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_booking_outcome_repository_appends_result_and_reconciliation_separately() -> None:
    result_connection = _WriteConnection()
    reconciliation_connection = _WriteConnection()
    connections = iter((result_connection, reconciliation_connection))

    @contextmanager
    def connection_factory() -> Iterator[_WriteConnection]:
        yield next(connections)

    binding, command, result, reconciliation = _booking_records()
    repository = BookingOutcomeRepository(connection_factory, tenant_id="tenant-a")
    repository.append_booking_result(command=command, binding=binding, result=result)
    repository.append_booking_reconciliation(
        command=command,
        binding=binding,
        prior_result=result,
        reconciliation=reconciliation,
    )

    assert result_connection.commits == reconciliation_connection.commits == 1
    result_insert = result_connection.cursor_instance.executions[1][1]
    reconciliation_insert = reconciliation_connection.cursor_instance.executions[1][1]
    assert result_insert[1:5] == (
        "availability_booking",
        "booking_result",
        result["resultId"],
        1,
    )
    assert reconciliation_insert[1:5] == (
        "availability_booking",
        "booking_reconciliation",
        reconciliation["reconciliationId"],
        1,
    )
    assert json.loads(cast(str, result_insert[6])) == result
    assert json.loads(cast(str, reconciliation_insert[6])) == reconciliation


@pytest.mark.parametrize("reconciliation_result", ["still_unknown", "conflict_requires_resolution"])
def test_booking_outcome_repository_persists_nonterminal_reconciliation(
    reconciliation_result: str,
) -> None:
    connection = _WriteConnection()

    @contextmanager
    def connection_factory() -> Iterator[_WriteConnection]:
        yield connection

    binding, command, result, reconciliation = _booking_records()
    reconciliation["result"] = reconciliation_result
    reconciliation["appointmentRef"] = None
    reconciliation["appointmentVersion"] = None
    BookingOutcomeRepository(
        connection_factory, tenant_id="tenant-a"
    ).append_booking_reconciliation(
        command=command,
        binding=binding,
        prior_result=result,
        reconciliation=reconciliation,
    )
    assert connection.commits == 1


@pytest.mark.parametrize("target", ["result", "reconciliation"])
def test_booking_outcome_repository_rejects_semantic_mismatch_before_opening(
    target: str,
) -> None:
    opened = False

    @contextmanager
    def connection_factory() -> Iterator[_WriteConnection]:
        nonlocal opened
        opened = True
        yield _WriteConnection()

    binding, command, result, reconciliation = _booking_records()
    repository = BookingOutcomeRepository(connection_factory, tenant_id="tenant-a")
    with pytest.raises(ContractSemanticError):
        if target == "result":
            result["commandRef"]["recordId"] = "command-other"
            repository.append_booking_result(command=command, binding=binding, result=result)
        else:
            reconciliation["priorResultRef"]["recordId"] = "result-other"
            repository.append_booking_reconciliation(
                command=command,
                binding=binding,
                prior_result=result,
                reconciliation=reconciliation,
            )
    assert not opened


@pytest.mark.parametrize("failure", ["structural", "roles", "tenant", "command"])
def test_booking_outcome_repository_rejects_unadmitted_result_before_opening(
    failure: str,
) -> None:
    opened = False

    @contextmanager
    def connection_factory() -> Iterator[_WriteConnection]:
        nonlocal opened
        opened = True
        yield _WriteConnection()

    binding, command, result, _ = _booking_records()
    expected: type[Exception]
    if failure == "structural":
        result.pop("evidenceIds")
        expected = ContractViolation
    elif failure == "roles":
        command = result
        expected = ValueError
    elif failure == "tenant":
        result["tenantId"] = "tenant-other"
        expected = TenantIsolationViolation
    else:
        command["commandKind"] = "cancel"
        expected = ContractSemanticError
    with pytest.raises(expected):
        BookingOutcomeRepository(connection_factory, tenant_id="tenant-a").append_booking_result(
            command=command, binding=binding, result=result
        )
    assert not opened


def test_booking_outcome_repository_rolls_back_insert_failure() -> None:
    connection = _WriteConnection(fail_on_execute=2)

    @contextmanager
    def connection_factory() -> Iterator[_WriteConnection]:
        yield connection

    binding, command, result, _ = _booking_records()
    with pytest.raises(RuntimeError, match="second decision failure"):
        BookingOutcomeRepository(connection_factory, tenant_id="tenant-a").append_booking_result(
            command=command, binding=binding, result=result
        )
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_slot_set_repository_appends_validated_caller_supplied_record() -> None:
    connection = _WriteConnection()

    @contextmanager
    def connection_factory() -> Iterator[_WriteConnection]:
        yield connection

    policy, readiness, binding, snapshot, slot_set = _slot_set_records()
    SlotSetRepository(connection_factory, tenant_id="tenant-a").append_slot_set(
        policy=policy,
        readiness=readiness,
        binding=binding,
        snapshot=snapshot,
        slot_set=slot_set,
    )

    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert len(connection.cursor_instance.executions) == 2
    insert = connection.cursor_instance.executions[1][1]
    assert insert[1:6] == (
        "availability_booking",
        "slot_set",
        slot_set["slotSetId"],
        1,
        slot_set["schemaVersion"],
    )
    assert json.loads(cast(str, insert[6])) == slot_set


@pytest.mark.parametrize("failure", ["structural", "roles", "tenant", "context"])
def test_slot_set_repository_rejects_unadmitted_record_before_opening(failure: str) -> None:
    opened = False

    @contextmanager
    def connection_factory() -> Iterator[_WriteConnection]:
        nonlocal opened
        opened = True
        yield _WriteConnection()

    policy, readiness, binding, snapshot, slot_set = _slot_set_records()
    expected: type[Exception]
    if failure == "structural":
        slot_set.pop("inputDigest")
        expected = ContractViolation
    elif failure == "roles":
        snapshot = slot_set
        expected = ValueError
    elif failure == "tenant":
        snapshot["tenantId"] = "tenant-other"
        expected = TenantIsolationViolation
    else:
        slot_set["snapshotRef"]["recordId"] = "snapshot-other"
        expected = ContractSemanticError

    with pytest.raises(expected):
        SlotSetRepository(connection_factory, tenant_id="tenant-a").append_slot_set(
            policy=policy,
            readiness=readiness,
            binding=binding,
            snapshot=snapshot,
            slot_set=slot_set,
        )
    assert not opened


def test_slot_set_repository_rolls_back_insert_failure() -> None:
    connection = _WriteConnection(fail_on_execute=2)

    @contextmanager
    def connection_factory() -> Iterator[_WriteConnection]:
        yield connection

    policy, readiness, binding, snapshot, slot_set = _slot_set_records()
    with pytest.raises(RuntimeError, match="second decision failure"):
        SlotSetRepository(connection_factory, tenant_id="tenant-a").append_slot_set(
            policy=policy,
            readiness=readiness,
            binding=binding,
            snapshot=snapshot,
            slot_set=slot_set,
        )
    assert connection.commits == 0
    assert connection.rollbacks == 1
