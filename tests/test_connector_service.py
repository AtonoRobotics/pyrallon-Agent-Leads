from __future__ import annotations

from typing import Any

import pytest
from test_connector_gateway import _request

from buyer_ops_contracts.connector_service import ConnectorDenied, ConnectorGateway


class _Repository:
    def get(self, record_id: str) -> dict[str, object] | None:
        assert record_id == "grant-1"
        return {
            "recordType": "ConnectorGrant",
            "grantState": "active",
            "version": 3,
            "capabilities": ["send"],
        }

    def list_by_type(self, record_type: str) -> list[dict[str, Any]]:
        del record_type
        return []


def test_control_plane_connector_fails_closed_when_no_adapter_runtime_is_configured() -> None:
    gateway = ConnectorGateway(_Repository(), tenant_id="tenant-1")

    with pytest.raises(ConnectorDenied) as raised:
        gateway.invoke(_request(), permit_digest="sha256:" + "b" * 64)

    assert raised.value.code == "configuration_incomplete"
