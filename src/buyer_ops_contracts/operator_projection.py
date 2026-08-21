"""Fail-closed boundary for operator-surface/1.1.0 JourneyView projections."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import rfc8785

from .canonical_repository import CanonicalRepository
from .journey_state import _related_records, compile_journey_state
from .structural import validate_record


@dataclass(frozen=True, slots=True)
class JourneyViewDerivationPolicy:
    """Deployment-owned presentation bindings required by the view contract.

    The runtime contract defines state derivation, but recovery ownership and blocker
    categories are deployment values. Missing bindings are configuration_incomplete;
    this type intentionally has no defaults.
    """

    compiler_version: str
    blocker_bindings: Mapping[str, tuple[str, str]]

    def binding_for(self, code: str) -> tuple[str, str]:
        binding = self.blocker_bindings.get(code)
        if binding is None:
            raise ValueError(f"configuration_incomplete: blocker binding missing for {code}")
        category, recovery_owner = binding
        if not category or not recovery_owner:
            raise ValueError(f"configuration_incomplete: blocker binding incomplete for {code}")
        return category, recovery_owner


def journey_view_etag(
    *,
    tenant_id: str,
    principal_id: str,
    journey_id: str,
    canonical_version: int,
    compiler_version: str,
    view_payload: dict[str, Any],
) -> str:
    """Return the closure-defined ETag for a fully derived JourneyView payload."""

    if not tenant_id or not principal_id or not journey_id or not compiler_version:
        raise ValueError("JourneyView ETag identity fields are required")
    if canonical_version < 1:
        raise ValueError("canonical_version must be positive")
    material = {
        "tenant": tenant_id,
        "principal": principal_id,
        "journey": journey_id,
        "canonical_version": canonical_version,
        "compiler_version": compiler_version,
        "view_payload": view_payload,
    }
    return f"sha256:{hashlib.sha256(rfc8785.dumps(cast(Any, material))).hexdigest()}"


class OperatorProjection:
    """Operator projection remains unavailable until its derivation contract is published."""

    def __init__(
        self,
        repository: CanonicalRepository,
        *,
        tenant_id: str,
        derivation_policy: JourneyViewDerivationPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._tenant_id = tenant_id
        self._derivation_policy = derivation_policy

    def journey_view(
        self,
        *,
        journey_id: str,
        principal_id: str,
        observed_at: datetime | None = None,
        records: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if self._derivation_policy is None:
            raise KeyError("governed JourneyView projection rules are unavailable")
        snapshot = records if records is not None else self._repository.current_records()
        journey = next(
            (record for record in snapshot if record.get("id") == journey_id),
            None,
        )
        if journey is None or journey.get("recordType") != "BuyerJourney":
            raise KeyError("BuyerJourney not found")
        if journey.get("tenantId") != self._tenant_id:
            raise PermissionError("BuyerJourney tenant mismatch")
        if not principal_id:
            raise ValueError("principal_id is required")
        observed = (observed_at or datetime.now(UTC)).astimezone(UTC)
        compilation = compile_journey_state(
            tenant_id=self._tenant_id,
            journey_id=journey_id,
            canonical_version=int(journey["version"]),
            records=snapshot,
            observed_at=observed,
        )
        current = [
            record
            for record in snapshot
            if record.get("tenantId") == self._tenant_id
            and record.get("status") in {"active", "current"}
        ]
        related = _related_records(current, journey, journey_id)
        representation_state = {
            "unconfirmed": "not_represented",
            "not_represented": "not_represented",
            "agreement_pending": "pending",
            "represented": "represented",
            "non_representation_showing_only": "not_represented",
            "expired": "revoked",
            "terminated": "revoked",
            "conflict": "unknown",
        }.get(str(journey.get("representationState")), "unknown")
        orthogonal_states = {
            "journey": compilation.state["ingress_state"],
            "contactability": compilation.state["contactability_state"],
            "acknowledgment": compilation.state["acknowledgment_state"],
            "qualification": compilation.state["qualification_state"],
            "consultation": compilation.state["consultation_state"],
            "nurture": compilation.state["nurture_state"],
            "representation": representation_state,
        }
        source_records = {
            str(record.get("id")): record
            for record in current
            if record.get("recordType")
            in {
                "Evidence",
                "Assertion",
                "VerifiedFact",
                "Inference",
                "Memory",
                "DocumentArtifact",
            }
            and isinstance(record.get("id"), str)
            and isinstance(record.get("version"), int)
            and isinstance(record.get("status"), str)
            and isinstance(record.get("digest"), str)
            and isinstance(record.get("capturedAt", record.get("observedAt")), str)
        }
        evidence_refs = [
            _source_ref(source_records[evidence_id])
            for evidence_id in compilation.evidence_ids
            if evidence_id in source_records
        ]
        blockers = []
        for code in compilation.state["blocker_codes"]:
            category, recovery_owner = self._derivation_policy.binding_for(code)
            blockers.append(
                {
                    "code": code,
                    "category": category,
                    "recovery_owner": recovery_owner,
                    "evidence_refs": evidence_refs,
                }
            )

        view_payload: dict[str, Any] = {
            "message_type": "operator_journey_view",
            "schema_version": "operator-surface/1.1.0",
            "tenant_id": self._tenant_id,
            "principal_id": principal_id,
            "journey_id": journey_id,
            "canonical_version": int(journey["version"]),
            "generated_at": observed.isoformat().replace("+00:00", "Z"),
            "orthogonal_states": orthogonal_states,
            "blockers": blockers,
            "next_action_refs": [],
            "commitment_refs": _record_refs(related, {"Commitment"}),
            "qualification_refs": _record_refs(
                related, {"QualificationObservation", "AgreementQualification"}
            ),
            "consent_refs": _record_refs(related, {"ConsentGrant"}),
            "representation_refs": _record_refs(related, {"RepresentationRelationship"}),
            "appointment_refs": _record_refs(related, {"Appointment"}),
            "effect_attempt_refs": _record_refs(related, {"EffectAttempt"}),
            "briefing_items": _briefing_items(source_records),
        }
        view_payload["etag"] = journey_view_etag(
            tenant_id=self._tenant_id,
            principal_id=principal_id,
            journey_id=journey_id,
            canonical_version=int(journey["version"]),
            compiler_version=self._derivation_policy.compiler_version,
            view_payload={key: value for key, value in view_payload.items() if key != "etag"},
        )
        validate_record(view_payload, "operator_surface")
        return view_payload

    def list_journey_ids(self) -> list[str]:
        return [item["id"] for item in self._repository.list_by_type("BuyerJourney")]


def _record_refs(records: list[dict[str, Any]], record_types: set[str]) -> list[dict[str, Any]]:
    return [
        {
            "record_id": str(record["id"]),
            "record_type": str(record["recordType"]),
            "version": int(record["version"]),
            "status": str(record["status"]),
        }
        for record in records
        if record.get("recordType") in record_types
        and isinstance(record.get("id"), str)
        and isinstance(record.get("version"), int)
        and isinstance(record.get("status"), str)
    ]


def _source_ref(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": str(record["id"]),
        "record_type": str(record["recordType"]),
        "version": int(record["version"]),
        "digest": str(record["digest"]),
        "captured_at": str(record.get("capturedAt", record.get("observedAt"))),
    }


def _briefing_items(source_records: Mapping[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Project only evidence-backed facts into the agent briefing.

    This deliberately excludes raw inferences and unlinked operational records:
    every item must be traceable to one of the source types admitted by the
    operator-surface contract.  Unknown or contradictory records remain visible
    through their admitted epistemic state instead of being silently promoted.
    """
    states = {
        "Evidence": "evidence",
        "Assertion": "assertion",
        "VerifiedFact": "verified_fact",
        "Inference": "inference",
        "Memory": "memory",
        "DocumentArtifact": "evidence",
    }
    items: list[dict[str, Any]] = []
    for record_id in sorted(source_records):
        record = source_records[record_id]
        record_type = str(record["recordType"])
        summary = next(
            (
                str(record[key]).strip()
                for key in ("summary", "claim", "statement", "content", "title")
                if isinstance(record.get(key), str) and str(record[key]).strip()
            ),
            f"{record_type} {record_id}",
        )
        items.append(
            {
                "item_id": f"briefing:{record_id}",
                "label": record_type,
                "epistemic_state": states[record_type],
                "summary": summary,
                "source_refs": [_source_ref(record)],
                "time_sensitivity": "none",
            }
        )
    return items
