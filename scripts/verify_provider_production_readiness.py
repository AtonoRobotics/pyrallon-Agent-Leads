"""Fail closed until the deployed calendar and e-signature program is runnable.

This is deliberately a readiness verifier, not a fixture generator.  It reads
only public metadata from the control plane and names missing configuration
classes without printing credentials or provider payloads.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

OAUTH_ISSUERS = ("google", "microsoft", "docusign")
E2E_RECORDS = (
    "BUYER_OPS_CALENDAR_E2E_AVAILABILITY_JSON",
    "BUYER_OPS_CALENDAR_E2E_SNAPSHOT_JSON",
    "BUYER_OPS_CALENDAR_E2E_BOOKING_JSON",
    "BUYER_OPS_CALENDAR_E2E_RECONCILE_JSON",
    "BUYER_OPS_ESIGNATURE_E2E_PRESENT_JSON",
    "BUYER_OPS_ESIGNATURE_E2E_RECONCILE_JSON",
)
E2E_PERMITS = (
    "BUYER_OPS_CALENDAR_E2E_SNAPSHOT_INTENT_JSON",
    "BUYER_OPS_CALENDAR_E2E_BOOKING_INTENT_JSON",
    "BUYER_OPS_CALENDAR_E2E_BOOKING_REPLAY_INTENT_JSON",
    "BUYER_OPS_CALENDAR_E2E_RECONCILE_INTENT_JSON",
    "BUYER_OPS_ESIGNATURE_E2E_PRESENT_INTENT_JSON",
    "BUYER_OPS_ESIGNATURE_E2E_RECONCILE_INTENT_JSON",
)


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value
    return values


def _metadata(base: str, environment: dict[str, str]) -> dict[str, Any]:
    token = environment.get("BUYER_OPS_CONTROL_TOKEN", "")
    actor = environment.get("BUYER_OPS_E2E_ACTOR", "")
    if not token or not actor:
        raise RuntimeError("BUYER_OPS_CONTROL_TOKEN and BUYER_OPS_E2E_ACTOR are required")
    request = urllib.request.Request(
        base.rstrip("/") + "/v1/platform/oauth-clients",
        headers={"x-buyer-ops-token": token, "x-buyer-ops-actor": actor},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"OAuth metadata endpoint returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("OAuth metadata endpoint is unreachable") from exc
    if not isinstance(body, dict):
        raise RuntimeError("OAuth metadata endpoint returned an invalid response")
    return body


def _is_https_origin(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.params and not parsed.query


def _workload_providers(environment: dict[str, str]) -> tuple[set[str], list[str]]:
    raw = environment.get("BUYER_OPS_DIRECT_PROVIDER_ADAPTERS_JSON", "").strip()
    if not raw:
        return set(), []
    try:
        values = json.loads(raw)
    except json.JSONDecodeError:
        return set(), ["BUYER_OPS_DIRECT_PROVIDER_ADAPTERS_JSON must be valid JSON"]
    if not isinstance(values, list):
        return set(), ["BUYER_OPS_DIRECT_PROVIDER_ADAPTERS_JSON must be an array"]
    expected = {
        ("google_calendar", "google_service_account"): "google",
        ("microsoft_graph", "microsoft_client_certificate"): "microsoft",
        ("docusign", "docusign_jwt"): "docusign",
    }
    configured: set[str] = set()
    errors: list[str] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        provider = {"google": "google_calendar", "microsoft": "microsoft_graph"}.get(
            str(value.get("provider") or "").lower(), str(value.get("provider") or "").lower()
        )
        issuer = expected.get((provider, str(value.get("credentialMode") or "").lower()))
        if issuer is None:
            continue
        credential_env = value.get("credentialEnv")
        if not isinstance(credential_env, str) or not environment.get(credential_env, "").strip():
            errors.append(f"{issuer} workload identity credentialEnv is missing")
            continue
        if issuer == "google":
            subject_env = value.get("subjectEnv")
            if not isinstance(subject_env, str) or not environment.get(subject_env, "").strip():
                errors.append("google workload identity subjectEnv is missing")
                continue
        configured.add(issuer)
    return configured, errors


def readiness_errors(
    environment: dict[str, str], metadata: dict[str, Any] | None
) -> list[str]:
    errors: list[str] = []
    workload_issuers, workload_errors = _workload_providers(environment)
    errors.extend(workload_errors)
    delegated_issuers = set(OAUTH_ISSUERS) - workload_issuers
    public_origin = environment.get("OPERATOR_PUBLIC_URL", "").strip().rstrip("/")
    if delegated_issuers and not _is_https_origin(public_origin):
        errors.append("OPERATOR_PUBLIC_URL must be an HTTPS operator origin for provider callbacks")
    elif public_origin and metadata is not None:
        expected_callback = public_origin + "/api/connectors/callback"
        if metadata.get("publicOrigin") != public_origin:
            errors.append("deployed control plane publicOrigin does not match OPERATOR_PUBLIC_URL")
        if metadata.get("redirectUri") != expected_callback:
            errors.append("deployed control plane redirectUri is not the canonical callback URL")

    if metadata is not None:
        clients = metadata.get("clients")
        configured = {
            str(item.get("issuer"))
            for item in clients if isinstance(clients, list) and isinstance(item, dict)
            and str(item.get("configured")).lower() == "true"
        }
        missing = sorted(delegated_issuers - configured)
        if missing:
            errors.append("platform OAuth applications are not registered: " + ", ".join(missing))

    missing_records = [name for name in E2E_RECORDS if not environment.get(name, "").strip()]
    if missing_records:
        errors.append("published provider E2E records are missing: " + ", ".join(missing_records))
    missing_permits = [name for name in E2E_PERMITS if not environment.get(name, "").strip()]
    if missing_permits:
        errors.append("Habitat E2E effect intents are missing: " + ", ".join(missing_permits))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.environ.get("BUYER_OPS_PRODUCTION_BASE", ""))
    parser.add_argument("--env-file", type=Path, default=Path(".env.production"))
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()

    environment = _load_env_file(args.env_file)
    environment.update({key: value for key, value in os.environ.items() if value})
    metadata = None
    if not args.offline:
        if not args.base:
            print("provider production readiness failed: --base is required unless --offline", file=sys.stderr)
            return 1
        try:
            metadata = _metadata(args.base, environment)
        except RuntimeError as exc:
            print(f"provider production readiness failed: {exc}", file=sys.stderr)
            return 1

    errors = readiness_errors(environment, metadata)
    if errors:
        print("provider production readiness failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("provider production readiness passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
