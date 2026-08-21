from __future__ import annotations

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "provider_readiness", Path("scripts/verify_provider_production_readiness.py")
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _configured_environment() -> dict[str, str]:
    values = {"OPERATOR_PUBLIC_URL": "https://operator.example.test"}
    values.update({name: "{}" for name in MODULE.E2E_RECORDS})
    values.update({name: "{}" for name in MODULE.E2E_PERMITS})
    return values


def test_readiness_requires_real_provider_setup() -> None:
    errors = MODULE.readiness_errors({}, {"clients": []})
    assert any("OPERATOR_PUBLIC_URL" in error for error in errors)
    assert any("OAuth applications" in error for error in errors)
    assert any("E2E records" in error for error in errors)
    assert any("effect intents" in error for error in errors)


def test_readiness_accepts_complete_public_metadata() -> None:
    environment = _configured_environment()
    metadata = {
        "publicOrigin": environment["OPERATOR_PUBLIC_URL"],
        "redirectUri": environment["OPERATOR_PUBLIC_URL"] + "/api/connectors/callback",
        "clients": [{"issuer": issuer, "configured": "true"} for issuer in MODULE.OAUTH_ISSUERS],
    }
    assert MODULE.readiness_errors(environment, metadata) == []


def test_readiness_rejects_mismatched_deployed_callback() -> None:
    environment = _configured_environment()
    errors = MODULE.readiness_errors(
        environment,
        {"publicOrigin": "https://other.example.test", "redirectUri": "", "clients": []},
    )
    assert any("publicOrigin" in error for error in errors)
    assert any("redirectUri" in error for error in errors)
