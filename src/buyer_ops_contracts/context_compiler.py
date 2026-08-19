"""Deterministic, purpose-scoped context compilation for PKT-07."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .closure import validate_closure_semantics
from .errors import ContractViolation, Violation
from .structural import validate_record


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("context timestamps must include an offset")
    return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ContextCandidate:
    tenant_id: str
    source_record_id: str
    record_type: str
    version: int
    buyer_journey_ids: tuple[str, ...]
    allowed_principal_ids: tuple[str, ...]
    allowed_workflow_ids: tuple[str, ...]
    allowed_purposes: tuple[str, ...]
    allowed_action_classes: tuple[str, ...]
    valid_from: str
    valid_to: str | None
    status: str
    superseded: bool
    freshness: dict[str, Any]
    content: Any


class ContextSource(Protocol):
    def load_candidates(self, request: dict[str, Any]) -> list[ContextCandidate]: ...


class OutputRouteSource(Protocol):
    def load_current_mappings(
        self, tenant_id: str, action_class: str, policy_version: str
    ) -> list[dict[str, Any]]: ...


class ManifestSigner(Protocol):
    @property
    def key_id(self) -> str: ...

    def sign(self, material: bytes) -> str: ...


class Ed25519ManifestSigner:
    def __init__(self, key_id: str, private_key: Ed25519PrivateKey) -> None:
        if not key_id:
            raise ValueError("context manifest key_id is required")
        self._key_id = key_id
        self._private_key = private_key

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign(self, material: bytes) -> str:
        return base64.urlsafe_b64encode(self._private_key.sign(material)).decode().rstrip("=")


@dataclass(frozen=True, slots=True)
class ContextCompilation:
    packet: dict[str, Any] | None
    manifest: dict[str, Any] | None
    failure: dict[str, Any] | None


class ContextCompiler:
    def __init__(
        self, source: ContextSource, routes: OutputRouteSource, signer: ManifestSigner
    ) -> None:
        self._source = source
        self._routes = routes
        self._signer = signer

    def compile(self, request: dict[str, Any]) -> ContextCompilation:
        validate_record(request, "context")
        if request.get("recordType") != "ContextCompileRequest":
            raise ValueError("request must be a ContextCompileRequest")
        compiled_at = _timestamp(request["compileAt"])
        expires_at = _timestamp(request["expiresAt"])
        if expires_at <= compiled_at:
            raise ContractViolation(
                [
                    Violation(
                        "CONTEXT_EXPIRY_ORDER",
                        "$.expiresAt",
                        "context expiry must follow compilation time",
                    )
                ]
            )
        route = self._select_route(request, compiled_at)

        included: list[ContextCandidate] = []
        exclusions: list[dict[str, str]] = []
        required_types = set(request["requiredRecordTypes"])
        candidates = sorted(
            self._source.load_candidates(request),
            key=lambda item: (item.source_record_id, item.version),
        )
        for candidate in candidates:
            reason = self._exclusion_reason(candidate, request, compiled_at, required_types, route)
            if reason is None:
                included.append(candidate)
            else:
                exclusions.append({"sourceRecordId": candidate.source_record_id, "reason": reason})

        present_types = {candidate.record_type for candidate in included}
        missing = sorted(required_types - present_types)
        if missing:
            failure = {
                "schemaVersion": "context-failure/1.1.0",
                "recordType": "ContextFailure",
                "requestId": request["requestId"],
                "state": "context_insufficient",
                "missingRecordTypes": missing,
            }
            validate_record(failure, "context")
            return ContextCompilation(None, None, failure)

        sections: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        for record_type in sorted(present_types):
            members = [item for item in included if item.record_type == record_type]
            content = [item.content for item in members]
            sections.append(
                {
                    "sectionId": record_type,
                    "purpose": request["purpose"],
                    "sourceRecordIds": [item.source_record_id for item in members],
                    "contentDigest": _digest(content),
                    "content": content,
                }
            )
            for item in members:
                freshness = item.freshness
                stale = compiled_at >= _timestamp(freshness["freshUntil"])
                source_entry = {
                    "sectionId": record_type,
                    "sourceRecordId": item.source_record_id,
                    "recordType": item.record_type,
                    "version": item.version,
                    "contentDigest": _digest(item.content),
                    "freshnessRecordId": freshness["recordId"],
                    "freshnessRecordVersion": freshness["recordVersion"],
                    "observedAt": freshness["observedAt"],
                    "freshnessAt": freshness["freshnessAt"],
                    "freshUntil": freshness["freshUntil"],
                    "epistemicType": freshness["epistemicType"],
                    "stale": stale,
                }
                if stale:
                    source_entry["staleLabel"] = route["staleLabel"]
                sources.append(source_entry)

        output_route = {
            "mappingRecordId": route["recordId"],
            "mappingRecordVersion": route["recordVersion"],
            "outputClass": route["outputClass"],
            "policyVersion": route["policyVersion"],
            "requestedArtifactType": request["requestedArtifactType"],
            "requiredAuthorityClasses": route["requiredAuthorityClasses"],
            "groundingMode": route["groundingMode"],
            "effectEligibility": route["effectEligibility"],
            "staleEvidencePolicy": route["staleEvidencePolicy"],
        }
        if route["staleEvidencePolicy"] == "allow_labeled":
            output_route["staleLabel"] = route["staleLabel"]

        manifest_identity = {
            "requestId": request["requestId"],
            "tenantId": request["tenantId"],
            "principalId": request["principalId"],
            "buyerJourneyId": request["buyerJourneyId"],
            "workflowId": request["workflowId"],
            "actionClass": request["actionClass"],
            "purpose": request["purpose"],
            "compiledAt": request["compileAt"],
            "expiresAt": request["expiresAt"],
            "ontologyVersion": request["ontologyVersion"],
            "policyVersions": request["policyVersions"],
            "knowledgeVersions": request["knowledgeVersions"],
            "compilerVersion": request["compilerVersion"],
            "requiredRecordTypes": sorted(required_types),
            "sources": sources,
            "exclusions": exclusions,
            "outputRoute": output_route,
        }
        manifest_id = _digest(manifest_identity)
        packet = {
            "schemaVersion": "context-packet/1.0.0",
            "manifestId": manifest_id,
            "ontologyVersion": request["ontologyVersion"],
            "compiledAt": request["compileAt"],
            "expiresAt": request["expiresAt"],
            "sections": sections,
        }
        unsigned_manifest = {
            "schemaVersion": "context-manifest/1.1.0",
            "recordType": "ContextManifest",
            "manifestId": manifest_id,
            **manifest_identity,
            "packetDigest": _digest(packet),
        }
        manifest = {
            **unsigned_manifest,
            "signature": {
                "algorithm": "Ed25519",
                "keyId": self._signer.key_id,
                "value": self._signer.sign(_canonical_bytes(unsigned_manifest)),
            },
        }
        validate_record(manifest, "context")
        return ContextCompilation(packet, manifest, None)

    @staticmethod
    def _exclusion_reason(
        candidate: ContextCandidate,
        request: dict[str, Any],
        compiled_at: datetime,
        required_types: set[str],
        route: dict[str, Any],
    ) -> str | None:
        if candidate.tenant_id != request["tenantId"]:
            return "cross_tenant"
        freshness = candidate.freshness
        validate_record(freshness, "closure")
        validate_closure_semantics(freshness, now=compiled_at)
        if (
            freshness.get("recordType") != "ContextSourceFreshness"
            or freshness.get("tenantId") != request["tenantId"]
            or freshness.get("sourceRecordId") != candidate.source_record_id
            or freshness.get("status") != "current"
        ):
            return "inactive"
        if request["buyerJourneyId"] not in candidate.buyer_journey_ids:
            return "cross_buyer"
        if request["principalId"] not in candidate.allowed_principal_ids:
            return "principal_denied"
        if request["workflowId"] not in candidate.allowed_workflow_ids:
            return "workflow_denied"
        if request["purpose"] not in candidate.allowed_purposes:
            return "purpose_denied"
        if request["actionClass"] not in candidate.allowed_action_classes:
            return "action_denied"
        if compiled_at < _timestamp(candidate.valid_from):
            return "not_yet_effective"
        if candidate.valid_to is not None and compiled_at >= _timestamp(candidate.valid_to):
            return "expired"
        if candidate.status != "active":
            return "inactive"
        if candidate.superseded:
            return "superseded"
        if candidate.record_type not in required_types:
            return "record_type_not_requested"
        if freshness["epistemicType"] not in route["allowedEpistemicTypes"]:
            return "epistemic_denied"
        if (
            compiled_at >= _timestamp(freshness["freshUntil"])
            and route["staleEvidencePolicy"] != "allow_labeled"
        ):
            return "stale"
        return None

    def _select_route(self, request: dict[str, Any], compiled_at: datetime) -> dict[str, Any]:
        mappings = self._routes.load_current_mappings(
            request["tenantId"], request["actionClass"], request["outputPolicyVersion"]
        )
        if len(mappings) != 1:
            raise ValueError("action class must resolve to exactly one current output mapping")
        route = mappings[0]
        validate_record(route, "closure")
        validate_closure_semantics(route, now=compiled_at)
        if (
            route.get("recordType") != "OutputClassMapping"
            or route.get("tenantId") != request["tenantId"]
            or route.get("actionClass") != request["actionClass"]
            or route.get("policyVersion") != request["outputPolicyVersion"]
            or route.get("status") != "current"
        ):
            raise ValueError("output mapping scope mismatch")
        if _timestamp(route["effectiveFrom"]) > compiled_at:
            raise ValueError("output mapping not yet effective")
        if _timestamp(route["expiresAt"]) <= compiled_at:
            raise ValueError("output mapping expired")
        if request["requestedArtifactType"] not in route["allowedArtifactTypes"]:
            raise ValueError("requested artifact type is not allowed by output mapping")
        if not set(route["requiredAuthorityClasses"]) <= set(request["authorityClasses"]):
            raise ValueError("required output authority is missing")
        return route


def verify_context_manifest(manifest: dict[str, Any], public_key: Ed25519PublicKey) -> None:
    validate_record(manifest, "context")
    signature = manifest["signature"]
    unsigned = dict(manifest)
    unsigned.pop("signature")
    encoded = signature["value"]
    padding = "=" * (-len(encoded) % 4)
    try:
        public_key.verify(
            base64.urlsafe_b64decode(encoded + padding),
            _canonical_bytes(unsigned),
        )
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("context manifest signature mismatch") from exc


def verify_context_bundle(
    packet: dict[str, Any],
    manifest: dict[str, Any],
    public_key: Ed25519PublicKey,
) -> None:
    """Verify signed manifest identity and exact packet bytes as one reconstruction unit."""
    verify_context_manifest(manifest, public_key)
    if packet.get("manifestId") != manifest["manifestId"]:
        raise ValueError("context packet manifest identity mismatch")
    if _digest(packet) != manifest["packetDigest"]:
        raise ValueError("context packet digest mismatch")
    if packet.get("ontologyVersion") != manifest["ontologyVersion"]:
        raise ValueError("context ontology version mismatch")
    if packet.get("compiledAt") != manifest["compiledAt"]:
        raise ValueError("context compilation time mismatch")
    if packet.get("expiresAt") != manifest["expiresAt"]:
        raise ValueError("context expiry mismatch")
    sources_by_section: dict[str, list[dict[str, Any]]] = {}
    for source in manifest["sources"]:
        sources_by_section.setdefault(source["sectionId"], []).append(source)
    for section in packet["sections"]:
        if _digest(section["content"]) != section["contentDigest"]:
            raise ValueError("context section digest mismatch")
        sources = sources_by_section.get(section["sectionId"], [])
        if section["sourceRecordIds"] != [source["sourceRecordId"] for source in sources]:
            raise ValueError("context source identity mismatch")
        if len(section["content"]) != len(sources):
            raise ValueError("context source content cardinality mismatch")
        for content, source in zip(section["content"], sources, strict=True):
            if _digest(content) != source["contentDigest"]:
                raise ValueError("context source content digest mismatch")


def verify_context_admission(
    work_request: dict[str, Any],
    manifest: dict[str, Any],
    public_key: Ed25519PublicKey,
    *,
    now: str | datetime,
) -> None:
    """Fail closed before cognition when signed context is stale or out of scope."""
    from .semantic import SemanticPolicy, validate_semantics

    validate_record(work_request, "gateway")
    evaluated_at = _timestamp(now) if isinstance(now, str) else now.astimezone(UTC)
    verify_context_bundle(work_request["contextPacket"], manifest, public_key)
    if work_request["contextManifestId"] != manifest["manifestId"]:
        raise ValueError("context work manifest identity mismatch")
    for field_name in (
        "tenantId",
        "principalId",
        "buyerJourneyId",
        "workflowId",
        "actionClass",
    ):
        if work_request[field_name] != manifest[field_name]:
            raise ValueError(f"context scope mismatch: {field_name}")
    if _timestamp(manifest["compiledAt"]) > evaluated_at:
        raise ValueError("context bundle not yet effective")
    if _timestamp(manifest["expiresAt"]) <= evaluated_at:
        raise ValueError("context bundle expired")
    validate_semantics(work_request, SemanticPolicy(now=evaluated_at))
