from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

from buyer_ops_contracts.gateway_routing import (
    CapacityManager,
    GatewayRouter,
    RouteFailure,
    RouteSelection,
    RouteTransitionSelection,
    materialize_gateway_failure,
)


def test_router_selects_only_the_fixed_qualified_route(load_fixture) -> None:
    work = load_fixture("valid/cognitive_work_request.json")
    work["tenantId"] = "tenant-1"
    work["actionClass"] = "lead_qualification"
    work["routePolicyVersion"] = "cognitive-route/1"
    policy = load_fixture("valid/gateway_route_policy.json")
    identity = load_fixture("valid/gateway_credential_identity.json")
    profile = load_fixture("valid/gateway_capability_profile.json")

    result = GatewayRouter().select(
        work,
        policy,
        identities={identity["identityRef"]: identity},
        profiles={profile["profileVersion"]: profile},
        available_capacity={identity["identityRef"]: 1},
        now=datetime(2029, 12, 31, tzinfo=UTC),
    )

    assert result == RouteSelection(
        route_id="codex-subscription-primary",
        provider_id="openai",
        adapter_id="openai-codex-sdk",
        adapter_version="adapter/1.0.0",
        transport="codex_sdk",
        credential_identity_ref="cred-subscription-1",
        auth_class="subscription_oauth",
        billing_class="subscription",
        model_family="approved-codex-family",
        resolved_model_id="codex-model-pinned",
        capability_profile_version="codex-lead-qualification/3",
        evaluation_qualification_id="eval-2026-08-17-a",
    )


def test_missing_credential_returns_typed_blocked_auth_without_fallback(load_fixture) -> None:
    work = load_fixture("valid/cognitive_work_request.json")
    work.update(
        {
            "tenantId": "tenant-1",
            "actionClass": "lead_qualification",
            "routePolicyVersion": "cognitive-route/1",
        }
    )
    policy = load_fixture("valid/gateway_route_policy.json")

    result = GatewayRouter().select(
        work,
        policy,
        identities={},
        profiles={},
        available_capacity={},
        now=datetime(2029, 12, 31, tzinfo=UTC),
    )

    assert result == RouteFailure(
        state="blocked_auth",
        route_id="codex-subscription-primary",
        diagnostic="credential identity unavailable",
    )


def test_hosted_runtime_with_inseparable_writes_is_route_ineligible(load_fixture) -> None:
    work = load_fixture("valid/cognitive_work_request.json")
    work.update(
        {
            "tenantId": "tenant-1",
            "actionClass": "lead_qualification",
            "routePolicyVersion": "cognitive-route/1",
        }
    )
    policy = load_fixture("valid/gateway_route_policy.json")
    identity = load_fixture("valid/gateway_credential_identity.json")
    profile = load_fixture("valid/gateway_capability_profile.json")
    profile["writeCapabilitiesMechanicallyExcluded"] = False

    result = GatewayRouter().select(
        work,
        policy,
        identities={identity["identityRef"]: identity},
        profiles={profile["profileVersion"]: profile},
        available_capacity={identity["identityRef"]: 1},
        now=datetime(2029, 12, 31, tzinfo=UTC),
    )

    assert result == RouteFailure(
        state="blocked_policy",
        route_id="codex-subscription-primary",
        diagnostic="runtime write capabilities are not excluded",
    )


def test_configured_subscription_to_api_transition_records_route_classes(load_fixture) -> None:
    work = load_fixture("valid/cognitive_work_request.json")
    work.update(
        {
            "tenantId": "tenant-1",
            "actionClass": "lead_qualification",
            "routePolicyVersion": "cognitive-route/1",
            "contextManifestId": "manifest-new",
        }
    )
    work["contextPacket"]["manifestId"] = "manifest-new"
    policy = load_fixture("valid/gateway_route_policy.json")
    api_identity = load_fixture("valid/gateway_credential_identity.json")
    api_identity.update(
        {
            "identityRef": "cred-api-1",
            "authClass": "metered_api",
            "billingClass": "metered",
            "subjectType": "service_identity",
            "allowedModelFamilies": ["approved-openai-family"],
            "concurrencyLimit": 8,
        }
    )
    api_profile = load_fixture("valid/gateway_capability_profile.json")
    api_profile.update(
        {
            "profileVersion": "responses-lead-qualification/2",
            "adapterId": "openai-responses-api",
            "adapterVersion": "adapter/2.0.0",
            "transport": "responses_api",
            "modelFamily": "approved-openai-family",
            "resolvedModelIds": ["openai-model-pinned"],
            "concurrencyEnvelope": 8,
            "evaluationQualificationId": "eval-2026-08-17-b",
        }
    )

    result = GatewayRouter().transition(
        work,
        policy,
        from_route_id="codex-subscription-primary",
        cause="blocked_auth",
        previous_context_manifest_id="manifest-old",
        identities={"cred-api-1": api_identity},
        profiles={"responses-lead-qualification/2": api_profile},
        available_capacity={"cred-api-1": 1},
        now=datetime(2029, 12, 31, tzinfo=UTC),
        operator_authorized=False,
        transition_evidence_id="route-evidence-1",
    )

    assert result == RouteTransitionSelection(
        from_route_id="codex-subscription-primary",
        to_route_id="openai-api-secondary",
        cause="blocked_auth",
        evidence_id="route-evidence-1",
        selection=RouteSelection(
            route_id="openai-api-secondary",
            provider_id="openai",
            adapter_id="openai-responses-api",
            adapter_version="adapter/2.0.0",
            transport="responses_api",
            credential_identity_ref="cred-api-1",
            auth_class="metered_api",
            billing_class="metered",
            model_family="approved-openai-family",
            resolved_model_id="openai-model-pinned",
            capability_profile_version="responses-lead-qualification/2",
            evaluation_qualification_id="eval-2026-08-17-b",
        ),
    )


def test_unconfigured_route_transition_is_mechanically_rejected(load_fixture) -> None:
    work = load_fixture("valid/cognitive_work_request.json")
    work.update(
        {
            "tenantId": "tenant-1",
            "actionClass": "lead_qualification",
            "routePolicyVersion": "cognitive-route/1",
            "contextManifestId": "manifest-new",
        }
    )
    work["contextPacket"]["manifestId"] = "manifest-new"

    result = GatewayRouter().transition(
        work,
        load_fixture("valid/gateway_route_policy.json"),
        from_route_id="codex-subscription-primary",
        cause="schema_rejected",
        previous_context_manifest_id="manifest-old",
        identities={},
        profiles={},
        available_capacity={},
        now=datetime(2029, 12, 31, tzinfo=UTC),
        operator_authorized=False,
        transition_evidence_id="route-evidence-1",
    )

    assert result == RouteFailure(
        state="blocked_policy",
        route_id="codex-subscription-primary",
        diagnostic="route transition is not authorized",
    )


def test_route_policy_rejects_duplicate_route_identity(load_fixture) -> None:
    work = load_fixture("valid/cognitive_work_request.json")
    work.update(
        {
            "tenantId": "tenant-1",
            "actionClass": "lead_qualification",
            "routePolicyVersion": "cognitive-route/1",
        }
    )
    policy = load_fixture("valid/gateway_route_policy.json")
    policy["routes"][1]["routeId"] = policy["routes"][0]["routeId"]

    result = GatewayRouter().select(
        work,
        policy,
        identities={},
        profiles={},
        available_capacity={},
        now=datetime(2029, 12, 31, tzinfo=UTC),
    )

    assert result == RouteFailure(
        state="blocked_policy",
        route_id="codex-subscription-primary",
        diagnostic="route policy contains duplicate route identity",
    )


def test_route_rejection_materializes_durable_failure_with_deadline(load_fixture) -> None:
    work = load_fixture("valid/cognitive_work_request.json")
    work["deadline"] = "2030-01-01T10:05:00Z"

    failure = materialize_gateway_failure(
        work,
        RouteFailure("blocked_auth", "route-1", "credential identity unavailable"),
        attempt=2,
        occurred_at="2030-01-01T10:00:01Z",
        diagnostic_code="credential_unavailable",
        evidence_ids=("evidence-route-1",),
    )

    assert failure == {
        "schemaVersion": "cognitive-failure/1.0.0",
        "recordType": "GatewayFailure",
        "workId": "work-1",
        "attempt": 2,
        "state": "blocked_auth",
        "routePolicyVersion": "route/1.0.0",
        "routeId": "route-1",
        "contextManifestId": "manifest-1",
        "occurredAt": "2030-01-01T10:00:01Z",
        "deadline": "2030-01-01T10:05:00Z",
        "diagnosticCode": "credential_unavailable",
        "diagnosticEvidenceIds": ["evidence-route-1"],
        "substantiveWorkPending": True,
        "traceId": "trace-1",
    }


def test_capacity_lease_is_atomic_and_never_exceeds_identity_envelope() -> None:
    manager = CapacityManager()
    barrier = Barrier(2)

    def acquire(index: int) -> object:
        barrier.wait()
        return manager.acquire(
            "cred-subscription-1",
            identity_limit=1,
            capability_limit=1,
            invocation_id=f"invocation-{index}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(acquire, range(2)))

    leases = [result for result in results if result is not None]
    assert len(leases) == 1
    assert manager.in_use("cred-subscription-1") == 1
    manager.release(leases[0])
    assert manager.in_use("cred-subscription-1") == 0


def test_route_policy_rejects_transition_to_unknown_route(load_fixture) -> None:
    work = load_fixture("valid/cognitive_work_request.json")
    work.update(
        {
            "tenantId": "tenant-1",
            "actionClass": "lead_qualification",
            "routePolicyVersion": "cognitive-route/1",
        }
    )
    policy = load_fixture("valid/gateway_route_policy.json")
    policy["transitions"][0]["to"] = "unqualified-route"

    result = GatewayRouter().select(
        work,
        policy,
        identities={},
        profiles={},
        available_capacity={},
        now=datetime(2029, 12, 31, tzinfo=UTC),
    )

    assert result == RouteFailure(
        state="blocked_policy",
        route_id="codex-subscription-primary",
        diagnostic="route transition references an unknown route",
    )


def test_route_policy_rejects_ambiguous_transition_cause(load_fixture) -> None:
    work = load_fixture("valid/cognitive_work_request.json")
    work.update(
        {
            "tenantId": "tenant-1",
            "actionClass": "lead_qualification",
            "routePolicyVersion": "cognitive-route/1",
        }
    )
    policy = load_fixture("valid/gateway_route_policy.json")
    policy["transitions"].append(
        {
            **policy["transitions"][0],
            "allowedCauses": ["blocked_auth"],
        }
    )

    result = GatewayRouter().select(
        work,
        policy,
        identities={},
        profiles={},
        available_capacity={},
        now=datetime(2029, 12, 31, tzinfo=UTC),
    )

    assert result == RouteFailure(
        state="blocked_policy",
        route_id="codex-subscription-primary",
        diagnostic="route policy contains ambiguous transition cause",
    )
