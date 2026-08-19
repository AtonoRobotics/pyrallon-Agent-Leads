"""Provider-neutral proposal runtime seam and deterministic test adapter."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .gateway_routing import RouteFailure, RouteSelection
from .semantic import SemanticPolicy, validate_gateway_pair
from .structural import validate_record


@dataclass(frozen=True, slots=True)
class RuntimeDescriptor:
    provider_id: str
    adapter_id: str
    adapter_version: str
    transport: str


@dataclass(frozen=True, slots=True)
class AdapterResult:
    descriptor: RuntimeDescriptor
    candidate: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RuntimeObservation:
    invocation_id: str
    attempt: int
    provider_run_id: str | None
    started_at: str
    completed_at: str
    input_units: int | float
    output_units: int | float
    unit_type: str


class ProposalRuntime(Protocol):
    def descriptor(self) -> RuntimeDescriptor: ...

    def health(self, identity_ref: str) -> str: ...

    def invoke(self, request: dict[str, Any]) -> AdapterResult: ...

    def cancel(self, invocation_id: str) -> str: ...

    def usage(self, identity_ref: str) -> dict[str, int | float | str]: ...


class SimulatedProposalRuntime:
    """Deterministic adapter for contract tests; it has no provider or effect access."""

    def __init__(self, descriptor: RuntimeDescriptor, candidate: dict[str, Any]) -> None:
        self._descriptor = descriptor
        self._candidate = copy.deepcopy(candidate)

    def descriptor(self) -> RuntimeDescriptor:
        return self._descriptor

    def health(self, identity_ref: str) -> str:
        if not identity_ref:
            raise ValueError("credential identity reference is required")
        return "healthy"

    def invoke(self, request: dict[str, Any]) -> AdapterResult:
        validate_record(request, "gateway")
        return AdapterResult(self._descriptor, copy.deepcopy(self._candidate))

    def cancel(self, invocation_id: str) -> str:
        if not invocation_id:
            raise ValueError("invocation identity is required")
        return "cancelled"

    def usage(self, identity_ref: str) -> dict[str, int | float | str]:
        if not identity_ref:
            raise ValueError("credential identity reference is required")
        return {"inputUnits": 0, "outputUnits": 0, "unitType": "provider_units"}


class ProposalNormalizer:
    """Attach authoritative runtime evidence, then admit the closed proposal contract."""

    def normalize(
        self,
        work_request: dict[str, Any],
        result: AdapterResult,
        selection: RouteSelection,
        observation: RuntimeObservation,
        *,
        policy_now: datetime,
    ) -> dict[str, Any]:
        if "runtimeEvidence" in result.candidate:
            raise ValueError("provider candidate may not supply runtime evidence")
        expected_descriptor = RuntimeDescriptor(
            provider_id=selection.provider_id,
            adapter_id=selection.adapter_id,
            adapter_version=selection.adapter_version,
            transport=selection.transport,
        )
        if result.descriptor != expected_descriptor:
            raise ValueError("adapter descriptor does not match selected route")
        started_at = _timestamp(observation.started_at)
        completed_at = _timestamp(observation.completed_at)
        if completed_at < started_at:
            raise ValueError("runtime completion precedes start")
        evidence: dict[str, Any] = {
            "invocationId": observation.invocation_id,
            "attempt": observation.attempt,
            "routePolicyVersion": work_request["routePolicyVersion"],
            "routeId": selection.route_id,
            "providerId": selection.provider_id,
            "adapterId": selection.adapter_id,
            "adapterVersion": selection.adapter_version,
            "transport": selection.transport,
            "credentialIdentityRef": selection.credential_identity_ref,
            "authClass": selection.auth_class,
            "billingClass": selection.billing_class,
            "modelFamily": selection.model_family,
            "resolvedModelId": selection.resolved_model_id,
            "capabilityProfileVersion": selection.capability_profile_version,
            "evaluationQualificationId": selection.evaluation_qualification_id,
            "startedAt": observation.started_at,
            "completedAt": observation.completed_at,
            "usage": {
                "inputUnits": observation.input_units,
                "outputUnits": observation.output_units,
                "unitType": observation.unit_type,
            },
        }
        if observation.provider_run_id is not None:
            evidence["providerRunId"] = observation.provider_run_id
        proposal = copy.deepcopy(result.candidate)
        proposal["runtimeEvidence"] = evidence
        validate_record(proposal, "gateway")
        validate_gateway_pair(work_request, proposal, SemanticPolicy(now=policy_now))
        return proposal


def normalize_provider_failure(
    capability_profile: dict[str, Any], provider_error_code: str, *, route_id: str
) -> RouteFailure:
    """Map provider-specific failures to stable states without retaining raw diagnostics."""
    validate_record(capability_profile, "gateway_runtime")
    mapping = next(
        (
            item
            for item in capability_profile["failureMappings"]
            if item["providerErrorCode"] == provider_error_code
        ),
        None,
    )
    if mapping is None:
        return RouteFailure(
            capability_profile["unmappedProviderErrorState"],
            route_id,
            "unmapped_provider_error",
        )
    return RouteFailure(mapping["state"], route_id, mapping["diagnosticCode"])


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("runtime timestamp requires an offset")
    return parsed.astimezone(UTC)
