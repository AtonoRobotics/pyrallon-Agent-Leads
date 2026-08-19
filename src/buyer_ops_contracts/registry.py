import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


@dataclass(frozen=True, slots=True)
class RegisteredContract:
    name: str
    schema_id: str
    sha256: str
    schema: dict[str, Any]
    validator: Draft202012Validator


class ContractRegistry:
    """Loads only packaged, hash-pinned contracts; no runtime schema discovery."""

    def __init__(self) -> None:
        root = files("buyer_ops_contracts")
        manifest = json.loads(root.joinpath("contracts.manifest.json").read_text())
        contracts: dict[str, RegisteredContract] = {}
        for entry in manifest["contracts"]:
            raw = root.joinpath(entry["resource"]).read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != entry["sha256"]:
                raise RuntimeError(f"contract digest mismatch: {entry['name']}")
            schema = json.loads(raw)
            Draft202012Validator.check_schema(schema)
            contracts[entry["name"]] = RegisteredContract(
                name=entry["name"],
                schema_id=schema["$id"],
                sha256=digest,
                schema=schema,
                validator=Draft202012Validator(schema, format_checker=FormatChecker()),
            )
        self._contracts = contracts

    def get(self, name: str) -> RegisteredContract:
        try:
            return self._contracts[name]
        except KeyError as exc:
            raise KeyError(f"unsupported contract: {name}") from exc

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._contracts))
