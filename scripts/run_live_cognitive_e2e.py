"""Run a provider-backed cognitive invocation against the deployed control plane.

This command is intentionally fail-closed: missing route configuration, a blocked
credential, a provider failure, or a response without normalized runtime evidence
is a failed production proof rather than a successful degradation check.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any


def _post(
    base: str, token: str, tenant: str, actor: str, payload: dict[str, Any]
) -> tuple[int, Any]:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    request = urllib.request.Request(
        base.rstrip("/") + "/v1/cognition/invoke",
        data=raw,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-buyer-ops-token": token,
            "x-buyer-ops-tenant": tenant,
            "x-buyer-ops-actor": actor,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read()
            return response.status, json.loads(body.decode())
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            decoded: Any = json.loads(body.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded = body.decode(errors="replace")
        return exc.code, decoded


def _configured_object(name: str) -> dict[str, Any]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        raise RuntimeError(f"{name} is not configured")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} must be a JSON object")
    return value


def _work_request(policy: dict[str, Any], tenant: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    now_text = now.isoformat().replace("+00:00", "Z")
    expiry_text = (now + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    action_class = policy.get("actionClass")
    version = policy.get("version")
    if not isinstance(action_class, str) or not action_class:
        raise RuntimeError("cognitive route policy actionClass is missing")
    if not isinstance(version, str) or not version:
        raise RuntimeError("cognitive route policy version is missing")
    manifest_id = f"live-cognitive-manifest-{uuid.uuid4().hex}"
    return {
        "schemaVersion": "cognitive-work/1.1.0",
        "recordType": "CognitiveWorkRequest",
        "workId": f"live-cognitive-work-{uuid.uuid4().hex}",
        "tenantId": tenant,
        "principalId": os.environ.get("BUYER_OPS_E2E_ACTOR", "live-e2e-runner"),
        "buyerJourneyId": f"live-cognitive-journey-{uuid.uuid4().hex}",
        "workflowId": f"live-cognitive-workflow-{uuid.uuid4().hex}",
        "actionClass": action_class,
        "objective": "Prepare a grounded qualification response",
        "contextManifestId": manifest_id,
        "contextPacket": {
            "schemaVersion": "context-packet/1.0.0",
            "manifestId": manifest_id,
            "ontologyVersion": "buyer-ops/0.3.0",
            "compiledAt": now_text,
            "expiresAt": expiry_text,
            "sections": [
                {
                    "sectionId": "live-qualification-facts",
                    "purpose": "Ground the qualification proposal",
                    "contentDigest": "sha256:" + "a" * 64,
                    "content": {"buyer-stated-budget": "unknown"},
                    "sourceRecordIds": ["live-cognitive-evidence"],
                }
            ],
        },
        "contextSufficiencyContractVersion": "lead-qualification/1.0.0",
        "requiredProposalSchemaVersion": "cognitive-proposal/1.1.0",
        "routePolicyVersion": version,
        "retryBudget": {"maxAttempts": 1, "maxElapsedMs": 120000},
        "degradationPolicyVersion": "degradation/1.0.0",
        "traceId": f"live-cognitive-trace-{uuid.uuid4().hex}",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base", default=os.environ.get("BUYER_OPS_CONTROL_BASE", "http://127.0.0.1:18091")
    )
    args = parser.parse_args()
    token = os.environ.get("BUYER_OPS_CONTROL_TOKEN", "")
    tenant = os.environ.get("BUYER_OPS_E2E_TENANT", "tenant-1")
    actor = os.environ.get("BUYER_OPS_E2E_ACTOR", "live-e2e-runner")
    if not token:
        print("BUYER_OPS_CONTROL_TOKEN is required", file=sys.stderr)
        return 2
    try:
        policy = _configured_object("BUYER_OPS_COGNITIVE_ROUTE_POLICY_JSON")
        _configured_object("BUYER_OPS_COGNITIVE_IDENTITIES_JSON")
        _configured_object("BUYER_OPS_COGNITIVE_PROFILES_JSON")
        _configured_object("BUYER_OPS_COGNITIVE_RUNTIMES_JSON")
        status, response = _post(
            args.base, token, tenant, actor, {"workRequest": _work_request(policy, tenant)}
        )
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"cognitive production E2E configuration failed: {exc}", file=sys.stderr)
        return 1
    passed = (
        status == 200
        and isinstance(response, dict)
        and response.get("schemaVersion") == "cognitive-proposal/1.1.0"
        and isinstance(response.get("runtimeEvidence"), dict)
        and bool(response["runtimeEvidence"].get("routeId"))
        and bool(response["runtimeEvidence"].get("credentialIdentityRef"))
    )
    summary = {
        "passed": passed,
        "status": status,
        "recordType": response.get("recordType") if isinstance(response, dict) else None,
        "diagnosticCode": response.get("diagnosticCode") if isinstance(response, dict) else None,
        "routeId": response.get("runtimeEvidence", {}).get("routeId")
        if isinstance(response, dict) and isinstance(response.get("runtimeEvidence"), dict)
        else None,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
