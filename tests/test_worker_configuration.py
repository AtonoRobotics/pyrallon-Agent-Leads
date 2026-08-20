from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path

import pytest

from buyer_ops_contracts.worker_main import (
    _run,
    load_worker_configuration,
    validate_compiled_journey_state,
)

ROOT = Path(__file__).resolve().parents[1]


def _records() -> dict:
    return json.loads((ROOT / "tests/fixtures/closure/temporal_valid.json").read_text())


def test_worker_configuration_requires_complete_owner_supplied_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TEMPORAL_WORKER_CONFIGURATION_JSON", raising=False)
    with pytest.raises(ValueError, match="is required"):
        load_worker_configuration()

    configuration = _records()["WorkerConfiguration"]
    assert load_worker_configuration(json.dumps(configuration)) == configuration


def test_worker_configuration_rejects_partial_or_wrong_temporal_record() -> None:
    configuration = _records()["WorkerConfiguration"]
    partial = copy.deepcopy(configuration)
    partial.pop("max_cached_workflows")
    with pytest.raises(ValueError):
        load_worker_configuration(json.dumps(partial))

    with pytest.raises(ValueError, match="wrong message type"):
        load_worker_configuration(json.dumps(_records()["JourneyState"]))


def test_worker_process_refuses_unpublished_journey_state_compiler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BUYER_OPS_DATABASE_DSN", "postgresql://unused")
    monkeypatch.setenv("TEMPORAL_ADDRESS", os.environ.get("TEMPORAL_ADDRESS", "unconfigured"))
    monkeypatch.setenv("TEMPORAL_NAMESPACE", os.environ.get("TEMPORAL_NAMESPACE", "unconfigured"))
    with pytest.raises(SystemExit, match="JourneyState derivation is unavailable"):
        asyncio.run(_run())


def test_compiled_journey_state_must_bind_current_canonical_version() -> None:
    state = _records()["JourneyState"]
    assert validate_compiled_journey_state(state, journey_id="value", canonical_version=1) == state

    stale = copy.deepcopy(state)
    stale["canonical_version"] = 2
    with pytest.raises(ValueError, match="current canonical journey"):
        validate_compiled_journey_state(stale, journey_id="value", canonical_version=1)
