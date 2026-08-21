"""Fail fast unless the configured Temporal endpoint is reachable."""

from __future__ import annotations

import asyncio
import os

from temporalio.client import Client

from buyer_ops_contracts.temporal_endpoint import resolve_temporal_address


async def check() -> None:
    address = os.environ.get("TEMPORAL_ADDRESS", "").strip()
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "").strip()
    if not address or not namespace:
        raise SystemExit("TEMPORAL_ADDRESS and TEMPORAL_NAMESPACE are required")
    await Client.connect(resolve_temporal_address(address), namespace=namespace)
    print("Temporal connectivity verification passed")


if __name__ == "__main__":
    asyncio.run(check())
