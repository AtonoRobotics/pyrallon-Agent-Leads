"""Deterministic route eligibility for the Cognitive Runtime Gateway 1.1 contract."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from .semantic import SemanticPolicy, validate_semantics
from .structural import validate_record


@dataclass(frozen=True, slots=True)
class RouteSelection:
    route_id: str
    provider_id: str
    adapter_id: str
    adapter_version: str
    transport: str
    credential_identity_ref: str
    auth_class: str
    billing_class: str
    model_family: str
    resolved_model_id: str
    capability_profile_version: str
    evaluation_qualification_id: str


@dataclass(frozen=True, slots=True)
class RouteFailure:
    state: str
    route_id: str
    diagnostic: str


@dataclass(frozen=True, slots=True)
class RouteTransitionSelection:
    from_route_id: str
    to_route_id: str
    cause: str
    evidence_id: str | None
    selection: RouteSelection


@dataclass(frozen=True, slots=True)
class CapacityLease:
    lease_id: str
    identity_ref: str
    invocation_id: str


class CapacityManager:
    """Atomic in-process lease manager used by deterministic adapter contract tests."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._leases: dict[str, CapacityLease] = {}
        self._sequence = 0

    def acquire(
        self,
        identity_ref: str,
        *,
        identity_limit: int,
        capability_limit: int,
        invocation_id: str,
    ) -> CapacityLease | None:
        if identity_limit < 1 or capability_limit < 1:
            raise ValueError("capacity limits must be positive")
        with self._lock:
            active = sum(lease.identity_ref == identity_ref for lease in self._leases.values())
            if active >= min(identity_limit, capability_limit):
                return None
            self._sequence += 1
            lease = CapacityLease(
                lease_id=f"capacity-{self._sequence}",
                identity_ref=identity_ref,
                invocation_id=invocation_id,
            )
            self._leases[lease.lease_id] = lease
            return lease

    def release(self, lease: CapacityLease) -> None:
        with self._lock:
            registered = self._leases.get(lease.lease_id)
            if registered != lease:
                raise ValueError("capacity lease is not active")
            del self._leases[lease.lease_id]

    def in_use(self, identity_ref: str) -> int:
        with self._lock:
            return sum(lease.identity_ref == identity_ref for lease in self._leases.values())


def materialize_gateway_failure(
    work_request: dict[str, Any],
    rejection: RouteFailure,
    *,
    attempt: int,
    occurred_at: str,
    diagnostic_code: str,
    evidence_ids: tuple[str, ...],
) -> dict[str, Any]:
    """Create the durable, secret-free failure record retained by Temporal/PostgreSQL."""
    failure: dict[str, Any] = {
        "schemaVersion": "cognitive-failure/1.0.0",
        "recordType": "GatewayFailure",
        "workId": work_request["workId"],
        "attempt": attempt,
        "state": rejection.state,
        "routePolicyVersion": work_request["routePolicyVersion"],
        "routeId": rejection.route_id,
        "contextManifestId": work_request["contextManifestId"],
        "occurredAt": occurred_at,
        "diagnosticCode": diagnostic_code,
        "diagnosticEvidenceIds": list(evidence_ids),
        "substantiveWorkPending": True,
        "traceId": work_request["traceId"],
    }
    if "deadline" in work_request:
        failure["deadline"] = work_request["deadline"]
    validate_record(failure, "gateway_runtime")
    return failure


class GatewayRouter:
    """Select the configured route without provider discovery or model-content access."""

    def select(
        self,
        work_request: dict[str, Any],
        policy: dict[str, Any],
        *,
        identities: Mapping[str, dict[str, Any]],
        profiles: Mapping[str, dict[str, Any]],
        available_capacity: Mapping[str, int],
        now: datetime,
    ) -> RouteSelection | RouteFailure:
        validate_record(work_request, "gateway")
        validate_semantics(work_request, SemanticPolicy(now=now.astimezone(UTC)))
        validate_record(policy, "gateway_runtime")
        route_ids = [route["routeId"] for route in policy["routes"]]
        if len(route_ids) != len(set(route_ids)):
            return RouteFailure(
                "blocked_policy",
                route_ids[0],
                "route policy contains duplicate route identity",
            )
        if any(
            transition["from"] not in route_ids or transition["to"] not in route_ids
            for transition in policy["transitions"]
        ):
            return RouteFailure(
                "blocked_policy",
                route_ids[0],
                "route transition references an unknown route",
            )
        transition_keys = [
            (transition["from"], cause)
            for transition in policy["transitions"]
            for cause in transition["allowedCauses"]
        ]
        if len(transition_keys) != len(set(transition_keys)):
            return RouteFailure(
                "blocked_policy",
                route_ids[0],
                "route policy contains ambiguous transition cause",
            )
        if work_request["routePolicyVersion"] != policy["version"]:
            raise ValueError("route policy version mismatch")
        if work_request["tenantId"] != policy["tenantId"]:
            raise ValueError("route policy tenant mismatch")
        if work_request["actionClass"] != policy["actionClass"]:
            raise ValueError("route policy action class mismatch")

        route = policy["routes"][0]
        identity = identities.get(route["credentialIdentityRef"])
        if identity is None:
            return RouteFailure("blocked_auth", route["routeId"], "credential identity unavailable")
        profile = profiles.get(route["capabilityProfile"])
        if profile is None:
            return RouteFailure(
                "blocked_policy", route["routeId"], "capability profile unavailable"
            )
        validate_record(identity, "gateway_runtime")
        validate_record(profile, "gateway_runtime")
        try:
            self._validate_eligibility(work_request, route, identity, profile, now)
        except ValueError as exc:
            state, diagnostic = str(exc).split(": ", 1)
            return RouteFailure(state, route["routeId"], diagnostic)
        if available_capacity.get(identity["identityRef"], 0) < 1:
            return RouteFailure("blocked_capacity", route["routeId"], "no credential capacity")

        return RouteSelection(
            route_id=route["routeId"],
            provider_id=profile["providerId"],
            adapter_id=route["adapterId"],
            adapter_version=profile["adapterVersion"],
            transport=profile["transport"],
            credential_identity_ref=identity["identityRef"],
            auth_class=route["authClass"],
            billing_class=route["billingClass"],
            model_family=route["modelFamily"],
            resolved_model_id=profile["resolvedModelIds"][0],
            capability_profile_version=profile["profileVersion"],
            evaluation_qualification_id=profile["evaluationQualificationId"],
        )

    def transition(
        self,
        work_request: dict[str, Any],
        policy: dict[str, Any],
        *,
        from_route_id: str,
        cause: str,
        previous_context_manifest_id: str,
        identities: Mapping[str, dict[str, Any]],
        profiles: Mapping[str, dict[str, Any]],
        available_capacity: Mapping[str, int],
        now: datetime,
        operator_authorized: bool,
        transition_evidence_id: str | None,
    ) -> RouteTransitionSelection | RouteFailure:
        validate_record(policy, "gateway_runtime")
        configured = next(
            (
                item
                for item in policy["transitions"]
                if item["from"] == from_route_id and cause in item["allowedCauses"]
            ),
            None,
        )
        if configured is None:
            return RouteFailure(
                "blocked_policy", from_route_id, "route transition is not authorized"
            )
        if configured["requiresOperatorAtRuntime"] and not operator_authorized:
            return RouteFailure(
                "blocked_policy", from_route_id, "operator authorization is required"
            )
        if configured["evidenceRequired"] and not transition_evidence_id:
            return RouteFailure("blocked_policy", from_route_id, "transition evidence is required")
        if work_request["contextManifestId"] == previous_context_manifest_id:
            return RouteFailure(
                "context_insufficient",
                from_route_id,
                "route transition requires recompiled context",
            )
        target = next(
            (route for route in policy["routes"] if route["routeId"] == configured["to"]),
            None,
        )
        if target is None:
            return RouteFailure(
                "blocked_policy", from_route_id, "transition target is not configured"
            )
        target_policy = {**policy, "routes": [target], "transitions": []}
        selected = self.select(
            work_request,
            target_policy,
            identities=identities,
            profiles=profiles,
            available_capacity=available_capacity,
            now=now,
        )
        if isinstance(selected, RouteFailure):
            return selected
        return RouteTransitionSelection(
            from_route_id=from_route_id,
            to_route_id=target["routeId"],
            cause=cause,
            evidence_id=transition_evidence_id,
            selection=selected,
        )

    @staticmethod
    def _validate_eligibility(
        work: dict[str, Any],
        route: dict[str, Any],
        identity: dict[str, Any],
        profile: dict[str, Any],
        now: datetime,
    ) -> None:
        if identity["state"] not in {"active", "expiring"}:
            raise ValueError("blocked_auth: credential identity is not active")
        if "expiresAt" in identity:
            expires_at = datetime.fromisoformat(identity["expiresAt"].replace("Z", "+00:00"))
            if expires_at.astimezone(UTC) <= now.astimezone(UTC):
                raise ValueError("blocked_auth: credential identity expired")
        if identity["tenantId"] != work["tenantId"]:
            raise ValueError("blocked_policy: credential tenant mismatch")
        if work["actionClass"] not in identity["allowedActionClasses"]:
            raise ValueError("blocked_policy: credential action class mismatch")
        if route["modelFamily"] not in identity["allowedModelFamilies"]:
            raise ValueError("blocked_policy: credential model family mismatch")
        if route["authClass"] != identity["authClass"]:
            raise ValueError("blocked_policy: credential authentication class mismatch")
        if route["billingClass"] != identity["billingClass"]:
            raise ValueError("blocked_policy: credential billing class mismatch")
        if profile["state"] != "qualified":
            raise ValueError("blocked_policy: capability profile is not qualified")
        if profile["adapterId"] != route["adapterId"]:
            raise ValueError("blocked_policy: adapter mismatch")
        if profile["modelFamily"] != route["modelFamily"]:
            raise ValueError("blocked_policy: evaluated model family mismatch")
        if profile["evaluationQualificationId"] != route["evaluationQualification"]:
            raise ValueError("blocked_policy: evaluation qualification mismatch")
        if work["actionClass"] not in profile["qualifiedActionClasses"]:
            raise ValueError("blocked_policy: action class is not evaluated")
        if work["schemaVersion"] not in profile["supportedInputSchemaVersions"]:
            raise ValueError("blocked_policy: input schema is unsupported")
        if work["requiredProposalSchemaVersion"] not in profile["supportedOutputSchemaVersions"]:
            raise ValueError("blocked_policy: output schema is unsupported")
        if identity["dataPolicyVersion"] not in profile["dataPolicyVersions"]:
            raise ValueError("blocked_policy: data policy is incompatible")
        if not profile["structuredOutput"]:
            raise ValueError("blocked_policy: structured output is unavailable")
        if not profile["writeCapabilitiesMechanicallyExcluded"]:
            raise ValueError("blocked_policy: runtime write capabilities are not excluded")
