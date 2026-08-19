import copy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from buyer_ops_contracts.context_compiler import (
    ContextCandidate,
    ContextCompiler,
    Ed25519ManifestSigner,
    verify_context_admission,
    verify_context_bundle,
    verify_context_manifest,
)


def _request() -> dict:
    return {
        "schemaVersion": "context-compile/1.1.0",
        "recordType": "ContextCompileRequest",
        "requestId": "context-request-1",
        "tenantId": "tenant-1",
        "principalId": "principal-1",
        "buyerJourneyId": "journey-1",
        "workflowId": "workflow-1",
        "actionClass": "lead_qualification",
        "purpose": "buyer_qualification",
        "compileAt": "2030-01-01T00:00:00Z",
        "expiresAt": "2030-01-01T00:05:00Z",
        "ontologyVersion": "buyer-ops/0.3.0",
        "policyVersions": {"context": "context-policy/2"},
        "knowledgeVersions": ["tx-buyer-knowledge/5"],
        "compilerVersion": "context-compiler/1.0.0",
        "requiredRecordTypes": ["BuyerJourney", "Person"],
        "outputPolicyVersion": "route-policy/1",
        "requestedArtifactType": "qualification_summary",
        "authorityClasses": ["read_buyer_context"],
    }


def _candidate(
    record_id: str,
    record_type: str,
    *,
    tenant_id: str = "tenant-1",
    journey_ids: tuple[str, ...] = ("journey-1",),
    principal_ids: tuple[str, ...] = ("principal-1",),
    workflow_ids: tuple[str, ...] = ("workflow-1",),
    purposes: tuple[str, ...] = ("buyer_qualification",),
    actions: tuple[str, ...] = ("lead_qualification",),
    status: str = "active",
    superseded: bool = False,
    valid_to: str | None = None,
    fresh_until: str = "2030-01-01T00:10:00Z",
    epistemic_type: str = "verified_fact",
) -> ContextCandidate:
    return ContextCandidate(
        tenant_id=tenant_id,
        source_record_id=record_id,
        record_type=record_type,
        version=1,
        buyer_journey_ids=journey_ids,
        allowed_principal_ids=principal_ids,
        allowed_workflow_ids=workflow_ids,
        allowed_purposes=purposes,
        allowed_action_classes=actions,
        valid_from="2029-01-01T00:00:00Z",
        valid_to=valid_to,
        status=status,
        superseded=superseded,
        freshness={
            "schemaVersion": "open-019-024/1.1.0",
            "tenantId": tenant_id,
            "recordId": f"freshness-{record_id}",
            "recordVersion": 1,
            "observedAt": "2029-12-31T23:59:00Z",
            "effectiveFrom": "2029-12-31T23:59:00Z",
            "status": "current",
            "evidenceRefs": [f"source-observation-{record_id}"],
            "recordType": "ContextSourceFreshness",
            "sourceRecordId": record_id,
            "epistemicType": epistemic_type,
            "freshnessAt": "2029-12-31T23:59:00Z",
            "freshUntil": fresh_until,
        },
        content={"id": record_id, "recordType": record_type},
    )


class _Source:
    def __init__(self, candidates: list[ContextCandidate]) -> None:
        self.candidates = candidates

    def load_candidates(self, request: dict) -> list[ContextCandidate]:
        return self.candidates


def _mapping(*, stale_policy: str = "reject") -> dict:
    mapping = {
        "schemaVersion": "open-019-024/1.1.0",
        "tenantId": "tenant-1",
        "recordId": "output-route-1",
        "recordVersion": 1,
        "observedAt": "2029-12-31T23:58:00Z",
        "effectiveFrom": "2029-12-31T23:58:00Z",
        "expiresAt": "2030-01-02T00:00:00Z",
        "status": "current",
        "evidenceRefs": ["route-policy-evidence-1"],
        "recordType": "OutputClassMapping",
        "actionClass": "lead_qualification",
        "outputClass": "qualification_advice",
        "policyVersion": "route-policy/1",
        "allowedArtifactTypes": ["qualification_summary"],
        "requiredAuthorityClasses": ["read_buyer_context"],
        "allowedEpistemicTypes": ["verified_fact", "evidence"],
        "staleEvidencePolicy": stale_policy,
        "groundingMode": "all_claims_grounded",
        "effectEligibility": "none",
    }
    if stale_policy == "allow_labeled":
        mapping["staleLabel"] = "stale_source"
    return mapping


class _Routes:
    def __init__(self, mappings: list[dict] | None = None) -> None:
        self.mappings = mappings if mappings is not None else [_mapping()]

    def load_current_mappings(
        self, tenant_id: str, action_class: str, policy_version: str
    ) -> list[dict]:
        return self.mappings


def test_context_compiler_filters_scope_and_signs_deterministically() -> None:
    candidates = [
        _candidate("person-1", "Person"),
        _candidate("journey-1", "BuyerJourney"),
        _candidate("cross-tenant", "Person", tenant_id="tenant-2"),
        _candidate("cross-buyer", "Person", journey_ids=("journey-2",)),
        _candidate("wrong-principal", "Person", principal_ids=("principal-2",)),
        _candidate("wrong-workflow", "Person", workflow_ids=("workflow-2",)),
        _candidate("wrong-purpose", "Person", purposes=("marketing",)),
        _candidate("wrong-action", "Person", actions=("property_recommendation",)),
        _candidate("expired", "Person", valid_to="2029-12-31T23:59:59Z"),
        _candidate("inactive", "Person", status="inactive"),
        _candidate("superseded", "Person", superseded=True),
        _candidate("not-requested", "Commitment"),
    ]
    key = Ed25519PrivateKey.generate()
    signer = Ed25519ManifestSigner("context-key-1", key)

    first = ContextCompiler(_Source(candidates), _Routes(), signer).compile(_request())
    second = ContextCompiler(_Source(list(reversed(candidates))), _Routes(), signer).compile(
        _request()
    )

    assert first.failure is None
    assert first.packet == second.packet
    assert first.manifest == second.manifest
    assert first.packet is not None
    assert first.manifest is not None
    assert first.manifest["principalId"] == "principal-1"
    assert first.manifest["requiredRecordTypes"] == ["BuyerJourney", "Person"]
    assert first.manifest["outputRoute"]["mappingRecordId"] == "output-route-1"
    assert all(source["stale"] is False for source in first.manifest["sources"])
    assert [section["sectionId"] for section in first.packet["sections"]] == [
        "BuyerJourney",
        "Person",
    ]
    assert {entry["reason"] for entry in first.manifest["exclusions"]} == {
        "cross_tenant",
        "cross_buyer",
        "principal_denied",
        "workflow_denied",
        "purpose_denied",
        "action_denied",
        "expired",
        "inactive",
        "superseded",
        "record_type_not_requested",
    }
    verify_context_manifest(first.manifest, key.public_key())
    verify_context_bundle(first.packet, first.manifest, key.public_key())

    tampered = copy.deepcopy(first.packet)
    tampered["sections"][0]["content"][0]["id"] = "other-journey"
    with pytest.raises(ValueError, match="packet digest mismatch"):
        verify_context_bundle(tampered, first.manifest, key.public_key())


def test_gateway_admission_binds_signed_context_scope_and_freshness(load_fixture) -> None:
    key = Ed25519PrivateKey.generate()
    result = ContextCompiler(
        _Source([_candidate("person-1", "Person"), _candidate("journey-1", "BuyerJourney")]),
        _Routes(),
        Ed25519ManifestSigner("context-key-1", key),
    ).compile(_request())
    assert result.packet is not None
    assert result.manifest is not None
    work = load_fixture("valid/cognitive_work_request.json")
    work.update(
        {
            "tenantId": "tenant-1",
            "principalId": "principal-1",
            "buyerJourneyId": "journey-1",
            "workflowId": "workflow-1",
            "actionClass": "lead_qualification",
            "contextManifestId": result.manifest["manifestId"],
            "contextPacket": result.packet,
        }
    )

    verify_context_admission(
        work,
        result.manifest,
        key.public_key(),
        now="2030-01-01T00:04:59Z",
    )

    for field in ("tenantId", "principalId", "buyerJourneyId", "workflowId", "actionClass"):
        mismatched = copy.deepcopy(work)
        mismatched[field] = "other"
        with pytest.raises(ValueError, match=f"context scope mismatch: {field}"):
            verify_context_admission(
                mismatched,
                result.manifest,
                key.public_key(),
                now="2030-01-01T00:04:59Z",
            )

    with pytest.raises(ValueError, match="context bundle expired"):
        verify_context_admission(
            work,
            result.manifest,
            key.public_key(),
            now="2030-01-01T00:05:00Z",
        )


def test_context_compiler_returns_typed_insufficiency_without_packet() -> None:
    key = Ed25519PrivateKey.generate()
    result = ContextCompiler(
        _Source([_candidate("person-1", "Person")]),
        _Routes(),
        Ed25519ManifestSigner("context-key-1", key),
    ).compile(_request())

    assert result.packet is None
    assert result.manifest is None
    assert result.failure == {
        "schemaVersion": "context-failure/1.1.0",
        "recordType": "ContextFailure",
        "requestId": "context-request-1",
        "state": "context_insufficient",
        "missingRecordTypes": ["BuyerJourney"],
    }


def test_context_route_fails_closed_when_missing_or_ambiguous() -> None:
    signer = Ed25519ManifestSigner("context-key-1", Ed25519PrivateKey.generate())
    for mappings in ([], [_mapping(), {**_mapping(), "recordId": "output-route-2"}]):
        with pytest.raises(ValueError, match="exactly one current output mapping"):
            ContextCompiler(_Source([]), _Routes(mappings), signer).compile(_request())


def test_stale_context_is_rejected_or_cryptographically_labeled_by_route() -> None:
    signer = Ed25519ManifestSigner("context-key-1", Ed25519PrivateKey.generate())
    stale_candidates = [
        _candidate("person-1", "Person", fresh_until="2030-01-01T00:00:00Z"),
        _candidate("journey-1", "BuyerJourney"),
    ]
    rejected = ContextCompiler(_Source(stale_candidates), _Routes(), signer).compile(_request())
    assert rejected.failure is not None
    assert rejected.manifest is None

    admitted = ContextCompiler(
        _Source(stale_candidates), _Routes([_mapping(stale_policy="allow_labeled")]), signer
    ).compile(_request())
    assert admitted.manifest is not None
    stale_source = next(
        source for source in admitted.manifest["sources"] if source["sourceRecordId"] == "person-1"
    )
    assert stale_source["stale"] is True
    assert stale_source["staleLabel"] == "stale_source"
