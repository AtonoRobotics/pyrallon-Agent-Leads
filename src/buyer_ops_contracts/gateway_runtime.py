"""Provider-neutral proposal runtime seam and deterministic test adapter."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

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


class ProviderRuntimeError(RuntimeError):
    """Provider failures safe for the durable gateway boundary."""

    def __init__(self, code: str, detail: str, *, retryable: bool) -> None:
        self.code = code
        self.detail = detail
        self.retryable = retryable
        super().__init__(detail)


class HttpsProposalRuntime:
    """Production provider-neutral runtime for a product-owned cognitive adapter.

    The gateway owns routing and evidence normalization. This adapter only transports
    already-authorized work to an HTTPS adapter and never persists or returns credentials.
    The adapter contract is intentionally small so provider implementations can be
    deployed independently without giving provider SDKs authority over canonical state.
    """

    def __init__(
        self,
        descriptor: RuntimeDescriptor,
        *,
        endpoint: str,
        identity_ref: str,
        credential: Callable[[str], str],
        timeout_seconds: float = 30.0,
    ) -> None:
        if descriptor.transport != "https":
            raise ValueError("production proposal runtime requires HTTPS transport")
        if not endpoint.startswith("https://"):
            raise ValueError("production proposal runtime endpoint must use HTTPS")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not identity_ref:
            raise ValueError("identity_ref is required")
        self._descriptor = descriptor
        self._endpoint = endpoint.rstrip("/")
        self._identity_ref = identity_ref
        self._credential = credential
        self._timeout = timeout_seconds

    def descriptor(self) -> RuntimeDescriptor:
        return self._descriptor

    def health(self, identity_ref: str) -> str:
        self._request("GET", "/health", identity_ref=identity_ref)
        return "healthy"

    def invoke(self, request: dict[str, Any]) -> AdapterResult:
        validate_record(request, "gateway")
        response = self._request("POST", "/invoke", identity_ref=self._identity_ref, body=request)
        candidate = response.get("candidate")
        if not isinstance(candidate, dict):
            raise ProviderRuntimeError(
                "invalid_provider_response",
                "provider response did not contain candidate",
                retryable=False,
            )
        provider_run_id = response.get("providerRunId")
        if provider_run_id is not None and (
            not isinstance(provider_run_id, str) or not provider_run_id
        ):
            raise ProviderRuntimeError(
                "invalid_provider_response",
                "providerRunId must be a non-empty string",
                retryable=False,
            )
        return AdapterResult(self._descriptor, candidate)

    def cancel(self, invocation_id: str) -> str:
        if not invocation_id:
            raise ValueError("invocation identity is required")
        response = self._request("POST", f"/invoke/{quote(invocation_id, safe='')}/cancel")
        status = response.get("status")
        if status not in {"cancelled", "already_complete"}:
            raise ProviderRuntimeError(
                "invalid_provider_response",
                "provider returned an invalid cancellation status",
                retryable=False,
            )
        return str(status)

    def usage(self, identity_ref: str) -> dict[str, int | float | str]:
        response = self._request("GET", "/usage", identity_ref=identity_ref)
        required = {"inputUnits", "outputUnits", "unitType"}
        if set(response) < required or not isinstance(response["unitType"], str):
            raise ProviderRuntimeError(
                "invalid_provider_response",
                "provider usage response is incomplete",
                retryable=False,
            )
        if not isinstance(response["inputUnits"], int | float) or not isinstance(
            response["outputUnits"], int | float
        ):
            raise ProviderRuntimeError(
                "invalid_provider_response", "provider usage units must be numeric", retryable=False
            )
        return {
            "inputUnits": response["inputUnits"],
            "outputUnits": response["outputUnits"],
            "unitType": response["unitType"],
        }

    def _request(
        self,
        method: str,
        suffix: str,
        *,
        identity_ref: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if identity_ref:
            token = self._credential(identity_ref)
            if not token:
                raise ProviderRuntimeError(
                    "credential_unavailable", "provider credential unavailable", retryable=False
                )
            headers["Authorization"] = f"Bearer {token}"
        request = Request(
            f"{self._endpoint}{suffix}",
            method=method,
            headers=headers,
            data=None if body is None else json.dumps(body, separators=(",", ":")).encode(),
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            retryable = exc.code == 429 or exc.code >= 500
            raise ProviderRuntimeError(
                "provider_http_error", f"provider returned HTTP {exc.code}", retryable=retryable
            ) from exc
        except (TimeoutError, URLError) as exc:
            raise ProviderRuntimeError(
                "provider_unavailable", "provider adapter unavailable", retryable=True
            ) from exc
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderRuntimeError(
                "invalid_provider_response", "provider response was not JSON", retryable=False
            ) from exc
        if not isinstance(decoded, dict):
            raise ProviderRuntimeError(
                "invalid_provider_response",
                "provider response must be a JSON object",
                retryable=False,
            )
        return decoded


class OpenAICompatibleProposalRuntime:
    """Execute governed proposals through an OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        descriptor: RuntimeDescriptor,
        *,
        endpoint: str,
        identity_ref: str,
        model_id: str,
        credential: Callable[[str], str],
        timeout_seconds: float = 60.0,
    ) -> None:
        if descriptor.transport != "https":
            raise ValueError("OpenAI-compatible runtime requires HTTPS transport")
        if not endpoint.startswith("https://"):
            raise ValueError("OpenAI-compatible runtime endpoint must use HTTPS")
        if not identity_ref or not model_id:
            raise ValueError("identity_ref and model_id are required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._descriptor = descriptor
        self._endpoint = endpoint.rstrip("/")
        self._identity_ref = identity_ref
        self._model_id = model_id
        self._credential = credential
        self._timeout = timeout_seconds
        self._usage: dict[str, int | float | str] = {
            "inputUnits": 0,
            "outputUnits": 0,
            "unitType": "tokens",
        }

    def descriptor(self) -> RuntimeDescriptor:
        return self._descriptor

    def health(self, identity_ref: str) -> str:
        self._request("GET", "/v1/models", identity_ref=identity_ref)
        return "healthy"

    def invoke(self, request: dict[str, Any]) -> AdapterResult:
        validate_record(request, "gateway")
        response = self._request(
            "POST",
            "/v1/chat/completions",
            identity_ref=self._identity_ref,
            body={
                "model": self._model_id,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Return only one JSON object matching the required proposal schema. "
                            "Do not include runtimeEvidence; the gateway supplies it."
                        ),
                    },
                    {"role": "user", "content": json.dumps(request, sort_keys=True)},
                ],
            },
        )
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderRuntimeError(
                "invalid_provider_response",
                "provider response choices are missing",
                retryable=False,
            )
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ProviderRuntimeError(
                "invalid_provider_response", "provider response content is missing", retryable=False
            )
        try:
            candidate = json.loads(_strip_json_fence(content))
        except (json.JSONDecodeError, TypeError) as exc:
            raise ProviderRuntimeError(
                "invalid_provider_response", "provider proposal was not valid JSON", retryable=False
            ) from exc
        if not isinstance(candidate, dict):
            raise ProviderRuntimeError(
                "invalid_provider_response", "provider proposal must be an object", retryable=False
            )
        usage = response.get("usage")
        if isinstance(usage, dict):
            self._usage = {
                "inputUnits": _provider_units(usage.get("prompt_tokens")),
                "outputUnits": _provider_units(usage.get("completion_tokens")),
                "unitType": "tokens",
            }
        return AdapterResult(self._descriptor, candidate)

    def cancel(self, invocation_id: str) -> str:
        if not invocation_id:
            raise ValueError("invocation identity is required")
        return "already_complete"

    def usage(self, identity_ref: str) -> dict[str, int | float | str]:
        if identity_ref != self._identity_ref:
            raise ValueError("credential identity reference does not match runtime")
        return dict(self._usage)

    def _request(
        self,
        method: str,
        suffix: str,
        *,
        identity_ref: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if identity_ref:
            token = self._credential(identity_ref)
            if not token:
                raise ProviderRuntimeError(
                    "credential_unavailable", "provider credential unavailable", retryable=False
                )
            headers["Authorization"] = f"Bearer {token}"
        request = Request(
            f"{self._endpoint}{suffix}",
            method=method,
            headers=headers,
            data=None if body is None else json.dumps(body, separators=(",", ":")).encode(),
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            raise ProviderRuntimeError(
                "provider_http_error",
                f"provider returned HTTP {exc.code}",
                retryable=exc.code == 429 or exc.code >= 500,
            ) from exc
        except (TimeoutError, URLError) as exc:
            raise ProviderRuntimeError(
                "provider_unavailable", "provider adapter unavailable", retryable=True
            ) from exc
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderRuntimeError(
                "invalid_provider_response", "provider response was not JSON", retryable=False
            ) from exc
        if not isinstance(decoded, dict):
            raise ProviderRuntimeError(
                "invalid_provider_response", "provider response must be an object", retryable=False
            )
        return decoded


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


def _strip_json_fence(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return stripped


def _provider_units(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return cast(int, value)


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
