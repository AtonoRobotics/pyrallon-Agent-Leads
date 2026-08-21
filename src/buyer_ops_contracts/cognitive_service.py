"""Production cognitive invocation composition over the governed gateway contracts."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .gateway_routing import (
    CapacityManager,
    GatewayRouter,
    RouteFailure,
    materialize_gateway_failure,
)
from .gateway_runtime import (
    ProposalNormalizer,
    ProposalRuntime,
    ProviderRuntimeError,
    RuntimeObservation,
    normalize_provider_failure,
)


@dataclass(frozen=True, slots=True)
class CognitiveRuntimeConfig:
    policy: dict[str, Any]
    identities: dict[str, dict[str, Any]]
    profiles: dict[str, dict[str, Any]]
    runtimes: dict[str, ProposalRuntime]


class CognitiveRuntimeService:
    """Execute one governed invocation and return only an admitted proposal or failure."""

    def __init__(
        self,
        config: CognitiveRuntimeConfig,
        *,
        capacity: CapacityManager | None = None,
        router: GatewayRouter | None = None,
        normalizer: ProposalNormalizer | None = None,
        credential_context: Any | None = None,
    ) -> None:
        self._config = config
        self._capacity = capacity or CapacityManager()
        self._router = router or GatewayRouter()
        self._normalizer = normalizer or ProposalNormalizer()
        self._credential_context = credential_context

    def invoke(
        self, work_request: dict[str, Any], *, now: datetime | None = None
    ) -> dict[str, Any]:
        current = now or datetime.now(UTC)
        context_token = (
            self._credential_context.set_tenant(str(work_request["tenantId"]))
            if self._credential_context is not None
            else None
        )
        invocation_id = f"invocation-{uuid.uuid4().hex}"
        try:
            selection = self._router.select(
                work_request,
                self._config.policy,
                identities=self._config.identities,
                profiles=self._config.profiles,
                available_capacity={
                    ref: int(identity.get("concurrencyLimit", 0))
                    for ref, identity in self._config.identities.items()
                },
                now=current,
            )
            if isinstance(selection, RouteFailure):
                return materialize_gateway_failure(
                    work_request,
                    selection,
                    attempt=1,
                    occurred_at=current.isoformat().replace("+00:00", "Z"),
                    diagnostic_code=selection.diagnostic,
                    evidence_ids=(f"routing-{work_request['workId']}",),
                )

            identity = self._config.identities[selection.credential_identity_ref]
            profile = self._config.profiles[selection.capability_profile_version]
            runtime = self._config.runtimes.get(selection.adapter_id)
            if runtime is None:
                return self._failure(
                    work_request,
                    selection.route_id,
                    "provider_runtime_unavailable",
                    current,
                )
            lease = self._capacity.acquire(
                selection.credential_identity_ref,
                identity_limit=int(identity["concurrencyLimit"]),
                capability_limit=int(profile["concurrencyEnvelope"]),
                invocation_id=invocation_id,
            )
            if lease is None:
                return self._failure(
                    work_request, selection.route_id, "capacity_exhausted", current
                )

            started = datetime.now(UTC)
            try:
                result = runtime.invoke(work_request)
                completed = datetime.now(UTC)
                usage = runtime.usage(selection.credential_identity_ref)
                input_units = _numeric_usage(usage, "inputUnits")
                output_units = _numeric_usage(usage, "outputUnits")
                observation = RuntimeObservation(
                    invocation_id=invocation_id,
                    attempt=1,
                    provider_run_id=None,
                    started_at=started.isoformat().replace("+00:00", "Z"),
                    completed_at=completed.isoformat().replace("+00:00", "Z"),
                    input_units=input_units,
                    output_units=output_units,
                    unit_type=str(usage["unitType"]),
                )
                return self._normalizer.normalize(
                    work_request,
                    result,
                    selection,
                    observation,
                    policy_now=current,
                )
            except ProviderRuntimeError as exc:
                rejection = normalize_provider_failure(
                    profile, exc.code, route_id=selection.route_id
                )
                return materialize_gateway_failure(
                    work_request,
                    rejection,
                    attempt=1,
                    occurred_at=current.isoformat().replace("+00:00", "Z"),
                    diagnostic_code=rejection.diagnostic,
                    evidence_ids=(f"runtime-failure-{work_request['workId']}",),
                )
            finally:
                self._capacity.release(lease)
        finally:
            if context_token is not None and self._credential_context is not None:
                self._credential_context.reset(context_token)

    @staticmethod
    def _failure(
        work_request: dict[str, Any], route_id: str, diagnostic: str, now: datetime
    ) -> dict[str, Any]:
        return materialize_gateway_failure(
            work_request,
            RouteFailure("provider_unavailable", route_id, diagnostic),
            attempt=1,
            occurred_at=now.isoformat().replace("+00:00", "Z"),
            diagnostic_code=diagnostic,
            evidence_ids=(f"runtime-failure-{work_request['workId']}",),
        )


def _json_object(name: str) -> dict[str, Any]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        raise ValueError(f"{name} is required")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain a JSON object")
    return value


def configuration_from_environment(
    credential_resolver: Callable[[str], str] | None = None,
) -> CognitiveRuntimeConfig:
    """Load only references and policy; credentials remain in dedicated environment vars."""
    from .gateway_runtime import (
        HttpsProposalRuntime,
        OpenAICompatibleProposalRuntime,
        RuntimeDescriptor,
    )

    policy = _json_object("BUYER_OPS_COGNITIVE_ROUTE_POLICY_JSON")
    identities_value = _json_object("BUYER_OPS_COGNITIVE_IDENTITIES_JSON")
    profiles_value = _json_object("BUYER_OPS_COGNITIVE_PROFILES_JSON")
    runtime_specs = _json_object("BUYER_OPS_COGNITIVE_RUNTIMES_JSON")
    identities = {
        str(key): value for key, value in identities_value.items() if isinstance(value, dict)
    }
    profiles = {str(key): value for key, value in profiles_value.items() if isinstance(value, dict)}
    runtimes: dict[str, ProposalRuntime] = {}
    for adapter_id, raw in runtime_specs.items():
        if not isinstance(raw, dict):
            raise ValueError("cognitive runtime specifications must be objects")
        credential_env = raw.get("credentialEnv")
        endpoint = raw.get("endpoint")
        identity_ref = raw.get("identityRef")
        if not all(
            isinstance(value, str) and value for value in (credential_env, endpoint, identity_ref)
        ):
            raise ValueError("cognitive runtime requires endpoint, identityRef, and credentialEnv")
        credential_env_name: str = str(credential_env)
        endpoint = str(endpoint)
        identity_ref = str(identity_ref)
        descriptor = RuntimeDescriptor(
            provider_id=str(raw.get("providerId", "")),
            adapter_id=str(adapter_id),
            adapter_version=str(raw.get("adapterVersion", "")),
            transport="https",
        )

        def credential(_identity: str, env_name: str = credential_env_name) -> str:
            if env_name == "database":
                if credential_resolver is None:
                    return ""
                return credential_resolver(_identity)
            return os.environ.get(env_name, "")

        runtime_class = (
            OpenAICompatibleProposalRuntime
            if raw.get("protocol") == "openai_compatible"
            else HttpsProposalRuntime
        )
        runtime_kwargs: dict[str, Any] = {
            "endpoint": endpoint,
            "identity_ref": identity_ref,
            "credential": credential,
            "timeout_seconds": float(raw.get("timeoutSeconds", 60)),
        }
        if runtime_class is OpenAICompatibleProposalRuntime:
            model_id = raw.get("modelId")
            if not isinstance(model_id, str) or not model_id:
                raise ValueError("OpenAI-compatible cognitive runtime requires modelId")
            runtime_kwargs["model_id"] = model_id
        runtimes[str(adapter_id)] = runtime_class(descriptor, **runtime_kwargs)
    return CognitiveRuntimeConfig(policy, identities, profiles, runtimes)


def _numeric_usage(usage: dict[str, int | float | str], key: str) -> int | float:
    value = usage.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ProviderRuntimeError(
            "invalid_provider_response", "provider usage is invalid", retryable=False
        )
    return value
