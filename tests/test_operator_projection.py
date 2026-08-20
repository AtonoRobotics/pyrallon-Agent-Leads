from __future__ import annotations

from typing import Any

import pytest

from buyer_ops_contracts.operator_projection import OperatorProjection


class _Repository:
    def __init__(self) -> None:
        self.journey = {
            "id": "journey-1",
            "tenantId": "tenant-1",
            "recordType": "BuyerJourney",
            "version": 7,
        }

    def get(self, record_id: str) -> dict[str, Any] | None:
        return self.journey if record_id == self.journey["id"] else None

    def list_by_type(self, record_type: str) -> list[dict[str, Any]]:
        return [self.journey] if record_type == "BuyerJourney" else []


def test_projection_fails_closed_without_published_assembler() -> None:
    projection = OperatorProjection(
        _Repository(),  # type: ignore[arg-type]
        tenant_id="tenant-1",
    )

    with pytest.raises(KeyError, match="projection rules are unavailable"):
        projection.journey_view(journey_id="journey-1", principal_id="agent-1")
