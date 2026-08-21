"""Production PostgreSQL backup/restore commands with verifiable manifests.

Restore is intentionally explicit and destructive: it requires a named input,
an explicit confirmation flag, and a separate target DSN. Secrets are passed
to PostgreSQL through the DSN/environment and never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"required PostgreSQL tool is unavailable: {name}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup(dsn: str, output: Path, *, runner: Callable[..., Any] = subprocess.run) -> Path:
    """Create a custom-format dump and adjacent integrity manifest."""
    if not dsn:
        raise ValueError("backup DSN is required")
    if output.exists():
        raise FileExistsError(f"backup output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    runner(
        [
            _require_tool("pg_dump"),
            "--dbname",
            dsn,
            "--format=custom",
            "--no-owner",
            "--file",
            str(output),
        ],
        check=True,
    )
    manifest = {
        "schemaVersion": "buyer-ops-postgres-backup/1.0.0",
        "createdAt": datetime.now(UTC).isoformat(),
        "artifact": output.name,
        "sha256": _sha256(output),
        "format": "custom",
    }
    manifest_path = output.with_suffix(output.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest_path


def verify(backup_path: Path, manifest_path: Path) -> dict[str, Any]:
    if not backup_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("backup and manifest are both required")
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, dict) or manifest.get("sha256") != _sha256(backup_path):
        raise ValueError("backup checksum does not match manifest")
    if manifest.get("artifact") != backup_path.name:
        raise ValueError("backup manifest artifact does not match backup")
    return manifest


def restore(
    dsn: str,
    backup_path: Path,
    manifest_path: Path,
    *,
    confirm: bool,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    """Restore a verified dump only after explicit destructive confirmation."""
    if not dsn:
        raise ValueError("restore DSN is required")
    if not confirm:
        raise PermissionError("restore requires --confirm-restore")
    verify(backup_path, manifest_path)
    runner(
        [
            _require_tool("pg_restore"),
            "--dbname",
            dsn,
            "--clean",
            "--if-exists",
            "--no-owner",
            str(backup_path),
        ],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="operation", required=True)
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--dsn", default=os.environ.get("BUYER_OPS_DATABASE_DSN", ""))
    backup_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("backup", type=Path)
    verify_parser.add_argument("manifest", type=Path)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--dsn", default=os.environ.get("BUYER_OPS_DATABASE_DSN", ""))
    restore_parser.add_argument("--backup", type=Path, required=True)
    restore_parser.add_argument("--manifest", type=Path, required=True)
    restore_parser.add_argument("--confirm-restore", action="store_true")
    args = parser.parse_args()
    try:
        if args.operation == "backup":
            print(backup(args.dsn, args.output))
        elif args.operation == "verify":
            verify(args.backup, args.manifest)
            print("postgres backup checksum verified")
        else:
            restore(args.dsn, args.backup, args.manifest, confirm=args.confirm_restore)
            print("postgres restore completed")
    except (
        FileExistsError,
        FileNotFoundError,
        PermissionError,
        RuntimeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"postgres backup/restore failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
