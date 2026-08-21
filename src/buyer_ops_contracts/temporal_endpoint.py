"""Resolve a shared Temporal endpoint for containers and host-run checks."""

from __future__ import annotations

import socket


def resolve_temporal_address(address: str) -> str:
    """Return a reachable endpoint without changing the configured identity.

    Compose maps ``host.docker.internal`` to its host gateway.  Linux hosts do
    not normally resolve that synthetic name, though, so deployment-verification
    scripts running directly on the host must use loopback for the same local
    Temporal endpoint.  Do not rewrite any other configured host.
    """
    configured = address.strip()
    host, separator, port = configured.rpartition(":")
    if host != "host.docker.internal" or not separator or not port:
        return configured
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return f"127.0.0.1:{port}"
    return configured
