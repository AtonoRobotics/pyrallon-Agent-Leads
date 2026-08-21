"""Verify the deployed local production control plane without creating effects."""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _control_token() -> str:
    token = os.environ.get("BUYER_OPS_CONTROL_TOKEN", "").strip()
    if token:
        return token
    env_path = Path(os.environ.get("BUYER_OPS_DEPLOYMENT_ENV_FILE", ".env.production"))
    if not env_path.is_file():
        return ""
    for line in env_path.read_text().splitlines():
        if line.startswith("BUYER_OPS_CONTROL_TOKEN="):
            return line.partition("=")[2].strip()
    return ""


def main() -> int:
    base = os.environ.get("BUYER_OPS_PRODUCTION_BASE", "http://127.0.0.1:18091").rstrip("/")
    headers = {"x-buyer-ops-token": _control_token()}
    request = urllib.request.Request(f"{base}/health", method="GET", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            status = int(response.status)
            body = response.read().decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError) as exc:
        print(f"production deployment verification failed: {exc}", file=sys.stderr)
        return 1
    if status != 200:
        print(f"production deployment verification failed: HTTP {status}", file=sys.stderr)
        return 1
    if '"status"' not in body and "healthy" not in body.lower():
        print(
            "production deployment verification failed: health payload is not healthy",
            file=sys.stderr,
        )
        return 1
    print("production deployment verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
