"""Mechanical release-gate and accessibility evidence evaluation."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import yaml

from .closure import validate_closure_semantics
from .digest import sha256_digest
from .errors import ContractViolation
from .structural import validate_record


class CapabilityDisablementVerifier(Protocol):
    def proves_disabled(self, capability_id: str, evidence_refs: list[str]) -> bool: ...


class ReleaseEvidenceRejected(RuntimeError):
    pass


def load_gate_registry(path: Path) -> tuple[dict[str, Any], str]:
    payload = path.read_bytes()
    registry = yaml.safe_load(payload)
    if not isinstance(registry, dict):
        raise ValueError("gate registry must be an object")
    return registry, f"sha256:{hashlib.sha256(payload).hexdigest()}"


class ReleaseEvidenceEvaluator:
    def __init__(
        self,
        registry: dict[str, Any],
        registry_digest: str,
        disablement: CapabilityDisablementVerifier,
        *,
        tenant_id: str,
    ) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._registry = registry
        self._registry_digest = registry_digest
        self._disablement = disablement
        self._tenant_id = tenant_id
        self._gates = {gate["id"]: gate for gate in registry["gates"]}

    @property
    def registry_version(self) -> str:
        return str(self._registry["registry_version"])

    @property
    def registry_digest(self) -> str:
        return self._registry_digest

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def required_gate_ids(self, directly_applicable_gate_ids: Iterable[str]) -> tuple[str, ...]:
        required: set[str] = set()

        def include(gate_id: str) -> None:
            if gate_id in required:
                return
            gate = self._gates.get(gate_id)
            if gate is None:
                raise ReleaseEvidenceRejected(f"unknown release gate: {gate_id}")
            required.add(gate_id)
            for dependency in gate["dependencies"]:
                include(dependency)

        for gate_id in directly_applicable_gate_ids:
            include(gate_id)
        return tuple(sorted(required))

    def evaluate(
        self,
        *,
        release_digest: str,
        directly_applicable_gate_ids: Iterable[str],
        evidence: Iterable[dict[str, Any]],
        now: datetime,
    ) -> tuple[str, ...]:
        evaluated_at = now.astimezone(UTC)
        required = self.required_gate_ids(directly_applicable_gate_ids)
        by_gate: dict[str, list[dict[str, Any]]] = {}
        for record in evidence:
            validate_record(record, "closure")
            validate_closure_semantics(record, now=evaluated_at)
            if record.get("recordType") != "ReleaseEvidence":
                raise ReleaseEvidenceRejected("non-release evidence supplied to gate evaluator")
            if record["tenantId"] != self._tenant_id:
                raise ReleaseEvidenceRejected("release evidence tenant mismatch")
            by_gate.setdefault(record["gateId"], []).append(record)

        accepted: list[str] = []
        for gate_id in required:
            gate = self._gates[gate_id]
            candidates = [
                record
                for record in by_gate.get(gate_id, [])
                if record["status"] == "current"
                and record["releaseDigest"] == release_digest
                and record["gateRegistryVersion"] == self._registry["registry_version"]
                and record["gateRegistryDigest"] == self._registry_digest
                and record["applicability"] == gate["class"]
                and record["scope"] == gate["scope"]
                and _timestamp(record["effectiveFrom"]) <= evaluated_at
                and _timestamp(record["expiresAt"]) > evaluated_at
            ]
            if len(candidates) != 1:
                raise ReleaseEvidenceRejected(
                    f"gate {gate_id} requires exactly one current release-bound evidence record"
                )
            record = candidates[0]
            if record["outcome"] == "pass":
                accepted.append(record["recordId"])
                continue
            if record["outcome"] == "not_applicable":
                if gate["class"] != "capability":
                    raise ReleaseEvidenceRejected(
                        f"gate {gate_id} cannot be not_applicable outside capability scope"
                    )
                if not self._disablement.proves_disabled(
                    record["capabilityId"], record["disabledCapabilityEvidenceRefs"]
                ):
                    raise ReleaseEvidenceRejected(
                        f"gate {gate_id} lacks verified capability-disablement evidence"
                    )
                accepted.append(record["recordId"])
                continue
            raise ReleaseEvidenceRejected(f"gate {gate_id} outcome is {record['outcome']}")
        return tuple(accepted)


def evaluate_accessibility_evidence(
    records: Iterable[dict[str, Any]],
    *,
    tenant_id: str,
    release_digest: str,
    deployed_builds: dict[str, str],
    now: datetime,
) -> tuple[str, ...]:
    if not tenant_id:
        raise ValueError("tenant_id is required")
    evaluated_at = now.astimezone(UTC)
    by_surface: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        validate_record(record, "closure")
        validate_closure_semantics(record, now=evaluated_at)
        if record.get("recordType") != "AccessibilityEvidence":
            raise ReleaseEvidenceRejected("non-accessibility record supplied")
        if record["tenantId"] != tenant_id:
            raise ReleaseEvidenceRejected("accessibility evidence tenant mismatch")
        by_surface.setdefault(record["surface"], []).append(record)
    accepted: list[str] = []
    for surface, build_digest in sorted(deployed_builds.items()):
        candidates = [
            record
            for record in by_surface.get(surface, [])
            if record["status"] == "current"
            and record["outcome"] == "current"
            and record["releaseDigest"] == release_digest
            and record["buildDigest"] == build_digest
            and _timestamp(record["effectiveFrom"]) <= evaluated_at
            and _timestamp(record["expiresAt"]) > evaluated_at
        ]
        if len(candidates) != 1:
            raise ReleaseEvidenceRejected(
                f"surface {surface} requires exactly one current build-bound WCAG evidence record"
            )
        accepted.append(candidates[0]["recordId"])
    return tuple(accepted)


def evaluate_accessibility_bindings(
    bindings: Iterable[dict[str, Any]],
    evidence: Iterable[dict[str, Any]],
    *,
    tenant_id: str,
    release_digest: str,
    deployed_builds: dict[str, str],
    acceptance_digests: dict[str, str],
    now: datetime,
) -> tuple[str, ...]:
    """Require one non-expired binding from each deployed surface to exact artifacts."""
    evaluated_at = now.astimezone(UTC)
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for record in evidence:
        validate_record(record, "closure")
        if record.get("recordType") != "AccessibilityEvidence":
            raise ReleaseEvidenceRejected("binding source must be accessibility evidence")
        if record["tenantId"] != tenant_id:
            raise ReleaseEvidenceRejected("accessibility evidence tenant mismatch")
        evidence_by_id[record["recordId"]] = record

    by_surface: dict[str, list[dict[str, Any]]] = {}
    for binding in bindings:
        try:
            validate_record(binding, "closure")
            validate_closure_semantics(binding, now=evaluated_at)
        except ContractViolation as exc:
            raise ReleaseEvidenceRejected(str(exc)) from exc
        if binding.get("recordType") != "AccessibilityBinding":
            raise ReleaseEvidenceRejected("non-binding record supplied")
        if binding["tenantId"] != tenant_id:
            raise ReleaseEvidenceRejected("accessibility binding tenant mismatch")
        if binding["releaseDigest"] != release_digest:
            raise ReleaseEvidenceRejected("accessibility binding release mismatch")
        source = evidence_by_id.get(binding["closureEvidenceRecordId"])
        if source is None or sha256_digest(source) != binding["closureEvidenceDigest"]:
            raise ReleaseEvidenceRejected("accessibility binding evidence digest mismatch")
        if binding["surface"] not in deployed_builds:
            raise ReleaseEvidenceRejected("accessibility binding surface is not deployed")
        if binding["buildDigest"] != deployed_builds[binding["surface"]]:
            raise ReleaseEvidenceRejected("accessibility binding build mismatch")
        if binding["operatorAcceptanceDigest"] != acceptance_digests.get(binding["surface"]):
            raise ReleaseEvidenceRejected("accessibility binding acceptance digest mismatch")
        by_surface.setdefault(binding["surface"], []).append(binding)

    accepted: list[str] = []
    for surface in sorted(deployed_builds):
        candidates = by_surface.get(surface, [])
        if len(candidates) != 1:
            raise ReleaseEvidenceRejected(
                f"surface {surface} requires exactly one current accessibility binding"
            )
        binding = candidates[0]
        if (
            _timestamp(binding["effectiveFrom"]) > evaluated_at
            or _timestamp(binding["expiresAt"]) <= evaluated_at
        ):
            raise ReleaseEvidenceRejected(f"surface {surface} accessibility binding is expired")
        source = evidence_by_id[binding["closureEvidenceRecordId"]]
        if (
            source["surface"] != surface
            or source["buildDigest"] != deployed_builds[surface]
            or source["releaseDigest"] != release_digest
            or source["status"] != "current"
            or source["outcome"] != "current"
        ):
            raise ReleaseEvidenceRejected(f"surface {surface} binding points to invalid evidence")
        accepted.append(binding["recordId"])
    return tuple(accepted)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("release evidence timestamp must include an offset")
    return parsed.astimezone(UTC)
