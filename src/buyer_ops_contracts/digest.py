"""Canonical JSON digest used by evidence, operator etags, and payload bindings."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(canonical_bytes(value)).hexdigest()}"
