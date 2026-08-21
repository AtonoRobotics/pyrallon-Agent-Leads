import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "postgres_backup_restore", Path("scripts/postgres_backup_restore.py")
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
backup = _MODULE.backup
restore = _MODULE.restore
verify = _MODULE.verify


def test_backup_writes_checksum_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "buyer-ops.dump"
    monkeypatch.setattr(_MODULE, "_require_tool", lambda name: f"/{name}")

    def runner(command, *, check):
        output.write_bytes(b"dump-bytes")

    manifest_path = backup("postgresql://localhost/buyer_ops", output, runner=runner)
    manifest = verify(output, manifest_path)
    assert manifest["artifact"] == output.name
    assert json.loads(manifest_path.read_text())["sha256"] == manifest["sha256"]


def test_restore_requires_confirmation_and_verified_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup_path = tmp_path / "buyer-ops.dump"
    backup_path.write_bytes(b"dump-bytes")
    manifest_path = tmp_path / "buyer-ops.dump.manifest.json"
    manifest_path.write_text(json.dumps({"artifact": backup_path.name, "sha256": ""}))
    monkeypatch.setattr(_MODULE, "_require_tool", lambda name: f"/{name}")
    with pytest.raises(PermissionError, match="confirm-restore"):
        restore(
            "postgresql://localhost/buyer_ops",
            backup_path,
            manifest_path,
            confirm=False,
            runner=lambda *a, **k: None,
        )
