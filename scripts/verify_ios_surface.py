"""Verify that the native iOS client is present and executable when Swift is installed."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "ios" / "BuyerOpsClient"
SOURCE = PACKAGE / "Sources" / "BuyerOpsClient" / "BuyerOpsClient.swift"
TESTS = PACKAGE / "Tests" / "BuyerOpsClientTests" / "BuyerOpsClientTests.swift"
REQUIRED_SYMBOLS = (
    "public actor BuyerOpsClient",
    "EncryptedFileSnapshotStore",
    "KeychainAccessTokenProvider",
    "reauthenticate",
    "pendingRefresh",
    "completeFileProtection",
    "PendingOperatorCommand",
    "OfflineCommandStore",
    "EncryptedFileCommandStore",
    "public func queue(command:",
    "public func reconnect()",
    "X-Buyer-Ops-Actor",
)


def main() -> int:
    errors: list[str] = []
    if not (PACKAGE / "Package.swift").is_file():
        errors.append("ios/BuyerOpsClient/Package.swift is missing")
    if not SOURCE.is_file():
        errors.append("native BuyerOpsClient source is missing")
    if not TESTS.is_file():
        errors.append("native BuyerOpsClient tests are missing")
    if SOURCE.is_file():
        source = SOURCE.read_text()
        errors.extend(
            f"native client is missing required behavior: {symbol}"
            for symbol in REQUIRED_SYMBOLS
            if symbol not in source
        )
    if errors:
        for error in errors:
            print(f"iOS surface verification failed: {error}", file=sys.stderr)
        return 1
    swift = shutil.which("swift")
    if swift is None:
        print(
            "iOS surface verification failed: Swift toolchain is required to compile BuyerOpsClient",
            file=sys.stderr,
        )
        return 1
    result = subprocess.run([swift, "test"], cwd=PACKAGE, check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
