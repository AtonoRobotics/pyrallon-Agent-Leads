"""Fail CI on high-confidence credential material in tracked implementation files."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "github_token": re.compile(r"\bgh[opsu]_[A-Za-z0-9]{36,255}\b"),
    "openai_key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
}


def tracked_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode() for item in completed.stdout.split(b"\0") if item]


def main() -> None:
    findings: list[str] = []
    for path in tracked_files():
        if not path.is_file() or path.stat().st_size > 5_000_000:
            continue
        try:
            content = path.read_text()
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(ROOT)
        for line_number, line in enumerate(content.splitlines(), start=1):
            for name, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append(f"{relative}:{line_number}: {name}")
    if findings:
        raise SystemExit("high-confidence secret patterns found:\n" + "\n".join(findings))
    print("high-confidence secret-pattern scan clean")


if __name__ == "__main__":
    main()
