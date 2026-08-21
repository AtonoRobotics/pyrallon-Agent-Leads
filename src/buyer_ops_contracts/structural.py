from typing import Any

from .errors import ContractViolation, Violation, path_string
from .registry import ContractRegistry, default_contract_registry


def validate_record(
    record: dict[str, Any], contract: str, registry: ContractRegistry | None = None
) -> None:
    active = registry or default_contract_registry()
    errors = sorted(active.get(contract).validator.iter_errors(record), key=lambda e: list(e.path))
    if errors:
        raise ContractViolation(
            [
                Violation("STRUCTURAL_SCHEMA", path_string(error.absolute_path), error.message)
                for error in errors
            ]
        )
