import copy
import json
from datetime import UTC, datetime
from pathlib import Path

from buyer_ops_contracts.cognitive_service import (
    CognitiveRuntimeConfig,
    CognitiveRuntimeService,
)
from buyer_ops_contracts.gateway_runtime import (
    ProviderRuntimeError,
    RuntimeDescriptor,
    SimulatedProposalRuntime,
)

ROOT = Path(__file__).resolve().parents[1]


def _fixture(name: str) -> dict:
    return json.loads((ROOT / "tests" / "fixtures" / "valid" / name).read_text())


def _config(runtime: object) -> tuple[dict, CognitiveRuntimeConfig]:
    work = _fixture("cognitive_work_request.json")
    work["actionClass"] = "lead_qualification"
    policy = _fixture("gateway_route_policy.json")
    policy["version"] = work["routePolicyVersion"]
    identity = _fixture("gateway_credential_identity.json")
    profile = _fixture("gateway_capability_profile.json")
    config = CognitiveRuntimeConfig(
        policy=policy,
        identities={identity["identityRef"]: identity},
        profiles={profile["profileVersion"]: profile},
        runtimes={profile["adapterId"]: runtime},  # type: ignore[dict-item]
    )
    return work, config


def test_service_invokes_runtime_and_attaches_authoritative_usage(load_fixture) -> None:
    candidate = copy.deepcopy(_fixture("cognitive_proposal.json"))
    candidate.pop("runtimeEvidence", None)
    candidate["actionClass"] = "lead_qualification"
    candidate["proposedActions"][0]["actionClass"] = "lead_qualification"
    runtime = SimulatedProposalRuntime(
        RuntimeDescriptor("openai", "openai-codex-sdk", "adapter/1.0.0", "codex_sdk"),
        candidate,
    )
    work, config = _config(runtime)

    proposal = CognitiveRuntimeService(config).invoke(
        work, now=datetime(2030, 1, 1, 10, 0, tzinfo=UTC)
    )

    assert proposal["schemaVersion"] == "cognitive-proposal/1.1.0"
    assert proposal["runtimeEvidence"]["routeId"] == "codex-subscription-primary"
    assert proposal["runtimeEvidence"]["usage"]["unitType"] == "provider_units"


class _FailingRuntime:
    def invoke(self, _request):
        raise ProviderRuntimeError("service_unavailable", "secret provider detail", retryable=True)

    def usage(self, _identity):
        raise AssertionError("usage must not be read after provider failure")


def test_service_materializes_provider_failure_without_raw_diagnostics() -> None:
    work, config = _config(_FailingRuntime())
    failure = CognitiveRuntimeService(config).invoke(
        work, now=datetime(2030, 1, 1, 10, 0, tzinfo=UTC)
    )

    assert failure["recordType"] == "GatewayFailure"
    assert failure["state"] == "provider_unavailable"
    assert "secret provider detail" not in json.dumps(failure)


def test_service_fails_closed_when_identity_is_missing() -> None:
    work, config = _config(object())
    config = CognitiveRuntimeConfig(config.policy, {}, config.profiles, config.runtimes)

    failure = CognitiveRuntimeService(config).invoke(work)

    assert failure["state"] == "blocked_auth"


class _CredentialContext:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def set_tenant(self, tenant_id: str) -> str:
        self.events.append(("set", tenant_id))
        return "token"

    def reset(self, token: str) -> None:
        self.events.append(("reset", token))


def test_service_scopes_database_credential_context_to_one_invocation() -> None:
    candidate = copy.deepcopy(_fixture("cognitive_proposal.json"))
    candidate.pop("runtimeEvidence", None)
    candidate["actionClass"] = "lead_qualification"
    candidate["proposedActions"][0]["actionClass"] = "lead_qualification"
    runtime = SimulatedProposalRuntime(
        RuntimeDescriptor("openai", "openai-codex-sdk", "adapter/1.0.0", "codex_sdk"),
        candidate,
    )
    work, config = _config(runtime)
    context = _CredentialContext()

    CognitiveRuntimeService(config, credential_context=context).invoke(
        work, now=datetime(2030, 1, 1, 10, 0, tzinfo=UTC)
    )

    assert context.events == [("set", work["tenantId"]), ("reset", "token")]
