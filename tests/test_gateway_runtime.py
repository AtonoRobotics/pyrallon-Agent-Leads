import copy
from datetime import UTC, datetime

from buyer_ops_contracts.gateway_routing import RouteSelection
from buyer_ops_contracts.gateway_runtime import (
    ProposalNormalizer,
    RuntimeDescriptor,
    RuntimeObservation,
    SimulatedProposalRuntime,
    normalize_provider_failure,
)


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
