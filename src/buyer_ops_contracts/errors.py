from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    path: str
    message: str


class ContractViolation(ValueError):
    """Fail-closed validation error with stable machine-readable violations."""

    def __init__(self, violations: list[Violation]) -> None:
        self.violations = tuple(violations)
        super().__init__("; ".join(f"{v.code} at {v.path}: {v.message}" for v in violations))


class SetupRejected(ValueError):
    """Stable fail-closed error returned by configuration and connector boundaries."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def path_string(path: Any) -> str:
    parts = [str(part) for part in path]
    return "$" if not parts else "$." + ".".join(parts)
