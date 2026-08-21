from __future__ import annotations

import socket

from buyer_ops_contracts.temporal_endpoint import resolve_temporal_address


def test_retains_reachable_or_non_special_temporal_endpoint() -> None:
    assert resolve_temporal_address("temporal.example:7233") == "temporal.example:7233"


def test_replaces_unresolvable_compose_host_alias(monkeypatch) -> None:
    def missing(*_args, **_kwargs):
        raise socket.gaierror("not resolvable")

    monkeypatch.setattr(
        "buyer_ops_contracts.temporal_endpoint.socket.getaddrinfo",
        missing,
    )
    assert resolve_temporal_address("host.docker.internal:7233") == "127.0.0.1:7233"
