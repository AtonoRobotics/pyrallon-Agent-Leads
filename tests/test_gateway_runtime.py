import copy
import json
from datetime import UTC, datetime

from buyer_ops_contracts.gateway_routing import RouteSelection
from buyer_ops_contracts.gateway_runtime import (
    HttpsProposalRuntime,
    OpenAICompatibleProposalRuntime,
    ProposalNormalizer,
    ProviderRuntimeError,
    RuntimeDescriptor,
    RuntimeObservation,
    SimulatedProposalRuntime,
    normalize_provider_failure,
)


class _Response:
    def __init__(self, body: dict) -> None:
        self._body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


def _selection(
    *,
    route_id: str,
    adapter_id: str,
    transport: str,
    auth_class: str,
    billing_class: str,
    model_family: str,
    model_id: str,
) -> RouteSelection:
    return RouteSelection(
        route_id=route_id,
        provider_id="simulated-provider",
        adapter_id=adapter_id,
        adapter_version="simulated/1.0.0",
        transport=transport,
        credential_identity_ref=f"credential-{route_id}",
        auth_class=auth_class,
        billing_class=billing_class,
        model_family=model_family,
        resolved_model_id=model_id,
        capability_profile_version=f"profile-{route_id}/1",
        evaluation_qualification_id=f"eval-{route_id}/1",
    )


def test_simulated_subscription_api_and_local_adapters_share_proposal_boundary(
    load_fixture,
) -> None:
    work = load_fixture("valid/cognitive_work_request.json")
    candidate = load_fixture("valid/cognitive_proposal.json")
    candidate.pop("runtimeEvidence")
    configurations = (
        ("subscription", "codex-sdk", "codex_sdk", "subscription_oauth", "subscription"),
        ("api", "responses-api", "responses_api", "metered_api", "metered"),
        ("local", "local-endpoint", "openai_compatible", "local_endpoint", "internal"),
    )

    proposals = []
    for route_id, adapter_id, transport, auth_class, billing_class in configurations:
        selection = _selection(
            route_id=route_id,
            adapter_id=adapter_id,
            transport=transport,
            auth_class=auth_class,
            billing_class=billing_class,
            model_family=f"family-{route_id}",
            model_id=f"model-{route_id}",
        )
        runtime = SimulatedProposalRuntime(
            RuntimeDescriptor(
                provider_id="simulated-provider",
                adapter_id=adapter_id,
                adapter_version="simulated/1.0.0",
                transport=transport,
            ),
            candidate,
        )
        result = runtime.invoke(work)
        proposal = ProposalNormalizer().normalize(
            work,
            result,
            selection,
            RuntimeObservation(
                invocation_id=f"invocation-{route_id}",
                attempt=1,
                provider_run_id=f"provider-run-{route_id}",
                started_at="2030-01-01T10:00:00Z",
                completed_at="2030-01-01T10:00:01Z",
                input_units=100,
                output_units=20,
                unit_type="tokens",
            ),
            policy_now=datetime(2029, 12, 31, tzinfo=UTC),
        )
        proposals.append(proposal)

    assert {proposal["schemaVersion"] for proposal in proposals} == {"cognitive-proposal/1.1.0"}
    assert [proposal["runtimeEvidence"]["billingClass"] for proposal in proposals] == [
        "subscription",
        "metered",
        "internal",
    ]
    assert all("providerResponse" not in proposal for proposal in proposals)
    assert all(proposal["workId"] == work["workId"] for proposal in proposals)


def test_provider_cannot_forge_gateway_runtime_evidence(load_fixture) -> None:
    work = load_fixture("valid/cognitive_work_request.json")
    candidate = copy.deepcopy(load_fixture("valid/cognitive_proposal.json"))
    runtime = SimulatedProposalRuntime(
        RuntimeDescriptor("simulated-provider", "codex-sdk", "simulated/1.0.0", "codex_sdk"),
        candidate,
    )
    selection = _selection(
        route_id="subscription",
        adapter_id="codex-sdk",
        transport="codex_sdk",
        auth_class="subscription_oauth",
        billing_class="subscription",
        model_family="family-subscription",
        model_id="model-subscription",
    )

    result = runtime.invoke(work)
    try:
        ProposalNormalizer().normalize(
            work,
            result,
            selection,
            RuntimeObservation(
                "invocation-1",
                1,
                None,
                "2030-01-01T10:00:00Z",
                "2030-01-01T10:00:01Z",
                1,
                1,
                "tokens",
            ),
            policy_now=datetime(2029, 12, 31, tzinfo=UTC),
        )
    except ValueError as exc:
        assert str(exc) == "provider candidate may not supply runtime evidence"
    else:
        raise AssertionError("forged runtime evidence was accepted")


def test_provider_errors_normalize_to_stable_states_without_raw_diagnostics(load_fixture) -> None:
    profile = load_fixture("valid/gateway_capability_profile.json")

    auth = normalize_provider_failure(profile, "auth_rejected", route_id="subscription")
    unknown = normalize_provider_failure(profile, "provider_internal_9182", route_id="subscription")

    assert (auth.state, auth.diagnostic) == ("blocked_auth", "credential_rejected")
    assert (unknown.state, unknown.diagnostic) == (
        "provider_unavailable",
        "unmapped_provider_error",
    )
    assert "provider_internal_9182" not in unknown.diagnostic


def test_https_runtime_transports_governed_operations_without_exposing_credential(
    monkeypatch,
) -> None:
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def fake_urlopen(request, timeout):
        calls.append((request.method, request.full_url, dict(request.header_items()), request.data))
        if request.full_url.endswith("/health"):
            return _Response({"status": "ok"})
        if request.full_url.endswith("/invoke"):
            return _Response({"candidate": {"schemaVersion": "cognitive-proposal/1.1.0"}})
        if request.full_url.endswith("/usage"):
            return _Response({"inputUnits": 4, "outputUnits": 2, "unitType": "tokens"})
        return _Response({"status": "cancelled"})

    monkeypatch.setattr("buyer_ops_contracts.gateway_runtime.urlopen", fake_urlopen)
    runtime = HttpsProposalRuntime(
        RuntimeDescriptor("provider", "adapter", "1.0.0", "https"),
        endpoint="https://adapter.example.test/v1",
        identity_ref="identity-1",
        credential=lambda identity: "secret-for-" + identity,
    )
    assert runtime.health("identity-1") == "healthy"
    assert runtime.cancel("invocation/1") == "cancelled"
    assert runtime.usage("identity-1") == {"inputUnits": 4, "outputUnits": 2, "unitType": "tokens"}
    for _method, url, headers, body in calls:
        assert "secret-for-identity-1" not in url
        if body is not None:
            assert b"secret-for-identity-1" not in body
        if "/health" in url or "/usage" in url:
            assert headers["Authorization"] == "Bearer secret-for-identity-1"


def test_https_runtime_rejects_invalid_provider_response(monkeypatch, load_fixture) -> None:
    monkeypatch.setattr(
        "buyer_ops_contracts.gateway_runtime.urlopen",
        lambda request, timeout: _Response({"candidate": []}),
    )
    runtime = HttpsProposalRuntime(
        RuntimeDescriptor("provider", "adapter", "1.0.0", "https"),
        endpoint="https://adapter.example.test/v1",
        identity_ref="identity-1",
        credential=lambda _identity: "token",
    )
    request = load_fixture("valid/cognitive_work_request.json")
    try:
        runtime.invoke(request)
    except ProviderRuntimeError as exc:
        assert exc.code == "invalid_provider_response"
        assert not exc.retryable
    else:
        raise AssertionError("invalid provider response was accepted")


def test_openai_compatible_runtime_parses_strict_json_and_records_usage(
    monkeypatch, load_fixture
) -> None:
    calls: list[tuple[str, bytes | None]] = []
    candidate = copy.deepcopy(load_fixture("valid/cognitive_proposal.json"))
    candidate.pop("runtimeEvidence")

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.data))
        if request.full_url.endswith("/v1/models"):
            return _Response({"data": []})
        return _Response(
            {
                "id": "chatcmpl-1",
                "choices": [
                    {"message": {"content": "```json\n" + json.dumps(candidate) + "\n```"}}
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            }
        )

    monkeypatch.setattr("buyer_ops_contracts.gateway_runtime.urlopen", fake_urlopen)
    runtime = OpenAICompatibleProposalRuntime(
        RuntimeDescriptor("openai", "openai-chat", "1.0.0", "https"),
        endpoint="https://api.openai.com",
        identity_ref="identity-1",
        model_id="gpt-production",
        credential=lambda _identity: "secret-token",
    )
    work = load_fixture("valid/cognitive_work_request.json")
    assert runtime.health("identity-1") == "healthy"
    result = runtime.invoke(work)
    assert result.candidate == candidate
    assert runtime.usage("identity-1") == {
        "inputUnits": 11,
        "outputUnits": 7,
        "unitType": "tokens",
    }
    assert calls[1][0].endswith("/v1/chat/completions")
    assert b"secret-token" not in (calls[1][1] or b"")
