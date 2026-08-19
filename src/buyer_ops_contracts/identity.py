"""Deterministic, tenant-scoped OT-01 external identity admission."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from psycopg.types.json import Jsonb

from .canonical_repository import CanonicalRepository, Connection, Cursor

IdentityKind = Literal["verified_email", "verified_phone", "provider_identity", "thread_lineage"]
ResolutionBasis = Literal[
    "verified_endpoint",
    "provider_identity",
    "external_mapping",
    "thread_lineage",
    "explicit_form_identity",
]
ResolutionOutcome = Literal["matched", "created", "ambiguous", "conflict", "suppressed"]


class IdentityCreationRequiresAtomicBundle(RuntimeError):
    """Raised when callers try to create only a mapping without the OT-01 canonical bundle."""


@dataclass(frozen=True, slots=True)
class IdentityMapping:
    tenant_id: str
    identity_fingerprint: str
    mapping_id: str
    version: int
    identity_kind: IdentityKind
    normalized_identity: str
    provider_account_ref: str | None
    purpose: str
    resolution_basis: ResolutionBasis
    resolution_authority_ref: str | None
    outcome: ResolutionOutcome
    person_id: str | None
    person_version: int | None
    resolution_case_id: str | None
    candidate_person_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    effective_from: datetime


def identity_fingerprint(
    *,
    identity_kind: IdentityKind,
    normalized_identity: str,
    provider_account_ref: str | None,
    purpose: str,
) -> str:
    if not normalized_identity or not purpose:
        raise ValueError("normalized identity and purpose are required")
    material = {
        "identityKind": identity_kind,
        "normalizedIdentity": normalized_identity,
        "providerAccountRef": provider_account_ref,
        "purpose": purpose,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class IdentityRepository:
    """Admit one deterministic mapping per normalized tenant/purpose identity key."""

    def __init__(self, connection: Connection, *, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._connection = connection
        self._tenant_id = tenant_id

    def admit(
        self,
        *,
        mapping_id: str,
        identity_kind: IdentityKind,
        normalized_identity: str,
        provider_account_ref: str | None,
        purpose: str,
        resolution_basis: ResolutionBasis,
        outcome: ResolutionOutcome,
        evidence_ids: tuple[str, ...],
        effective_from: datetime,
        person_id: str | None = None,
        person_version: int | None = None,
        resolution_case_id: str | None = None,
        candidate_person_ids: tuple[str, ...] = (),
        resolution_authority_ref: str | None = None,
    ) -> tuple[IdentityMapping, bool]:
        """Return `(mapping, created)`; concurrent duplicates return the admitted mapping."""
        if outcome == "created":
            raise IdentityCreationRequiresAtomicBundle(
                "created outcome requires admit_created_bundle with Person, endpoint, "
                "BuyingParty, BuyerJourney, Conversation, and attribution"
            )
        if not mapping_id or not evidence_ids:
            raise ValueError("mapping id and evidence are required")
        if outcome == "matched" and (person_id is None or person_version is None):
            raise ValueError("matched outcome requires a versioned canonical Person")
        if outcome in {"ambiguous", "conflict"}:
            if person_id is not None or person_version is not None:
                raise ValueError("ambiguous/conflict outcome cannot attach a Person")
            if resolution_case_id is None or not candidate_person_ids:
                raise ValueError("ambiguous/conflict outcome requires a case and candidates")
        fingerprint = identity_fingerprint(
            identity_kind=identity_kind,
            normalized_identity=normalized_identity,
            provider_account_ref=provider_account_ref,
            purpose=purpose,
        )
        proposed = IdentityMapping(
            tenant_id=self._tenant_id,
            identity_fingerprint=fingerprint,
            mapping_id=mapping_id,
            version=1,
            identity_kind=identity_kind,
            normalized_identity=normalized_identity,
            provider_account_ref=provider_account_ref,
            purpose=purpose,
            resolution_basis=resolution_basis,
            resolution_authority_ref=resolution_authority_ref,
            outcome=outcome,
            person_id=person_id,
            person_version=person_version,
            resolution_case_id=resolution_case_id,
            candidate_person_ids=candidate_person_ids,
            evidence_ids=evidence_ids,
            effective_from=effective_from.astimezone(UTC),
        )
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"identity:{self._tenant_id}:{fingerprint}",),
                )
                cursor.execute(
                    """
                    SELECT tenant_id, identity_fingerprint, mapping_id, version,
                        identity_kind, normalized_identity, provider_account_ref, purpose,
                        resolution_basis, resolution_authority_ref, outcome, person_id,
                        person_version, resolution_case_id, candidate_person_ids,
                        evidence_ids, effective_from
                    FROM external_identity_mappings_current
                    WHERE tenant_id = %s AND identity_fingerprint = %s
                    """.strip(),
                    (self._tenant_id, fingerprint),
                )
                row = cursor.fetchone()
                if row is not None:
                    admitted = self._row_to_mapping(row)
                    created = False
                else:
                    self._validate_people(cursor, proposed)
                    self._insert(cursor, proposed)
                    admitted = proposed
                    created = True
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return admitted, created

    def get_by_fingerprint(self, fingerprint: str) -> IdentityMapping | None:
        if not fingerprint:
            raise ValueError("fingerprint is required")
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    SELECT tenant_id, identity_fingerprint, mapping_id, version,
                        identity_kind, normalized_identity, provider_account_ref, purpose,
                        resolution_basis, resolution_authority_ref, outcome, person_id,
                        person_version, resolution_case_id, candidate_person_ids,
                        evidence_ids, effective_from
                    FROM external_identity_mappings_current
                    WHERE tenant_id = %s AND identity_fingerprint = %s
                    """.strip(),
                    (self._tenant_id, fingerprint),
                )
                row = cursor.fetchone()
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return None if row is None else self._row_to_mapping(row)

    def admit_created_bundle(
        self,
        *,
        canonical: CanonicalRepository,
        evidence: Mapping[str, Any],
        person: Mapping[str, Any],
        endpoint: Mapping[str, Any],
        party: Mapping[str, Any],
        journey: Mapping[str, Any],
        conversation: Mapping[str, Any],
        mapping_id: str,
        identity_kind: IdentityKind,
        normalized_identity: str,
        provider_account_ref: str | None,
        purpose: str,
        resolution_basis: ResolutionBasis,
        evidence_ids: tuple[str, ...],
        effective_from: datetime,
        resolution_authority_ref: str | None = None,
    ) -> IdentityMapping:
        """Create the OT-01 identity bundle in one transaction. Mapping-only create is refused."""
        if not mapping_id or not evidence_ids:
            raise ValueError("mapping id and evidence are required")
        if person.get("tenantId") != self._tenant_id:
            raise ValueError("person tenant does not match repository tenant")
        fingerprint = identity_fingerprint(
            identity_kind=identity_kind,
            normalized_identity=normalized_identity,
            provider_account_ref=provider_account_ref,
            purpose=purpose,
        )
        mapping = IdentityMapping(
            tenant_id=self._tenant_id,
            identity_fingerprint=fingerprint,
            mapping_id=mapping_id,
            version=1,
            identity_kind=identity_kind,
            normalized_identity=normalized_identity,
            provider_account_ref=provider_account_ref,
            purpose=purpose,
            resolution_basis=resolution_basis,
            resolution_authority_ref=resolution_authority_ref,
            outcome="created",
            person_id=str(person["id"]),
            person_version=int(person["version"]),
            resolution_case_id=None,
            candidate_person_ids=(),
            evidence_ids=evidence_ids,
            effective_from=effective_from.astimezone(UTC),
        )
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"identity:{self._tenant_id}:{fingerprint}",),
                )
                cursor.execute(
                    """
                    SELECT 1 FROM external_identity_mappings_current
                    WHERE tenant_id = %s AND identity_fingerprint = %s
                    """.strip(),
                    (self._tenant_id, fingerprint),
                )
                if cursor.fetchone() is not None:
                    raise ValueError("identity mapping already exists")
                for record in (evidence, person, endpoint, party, journey, conversation):
                    canonical._save_on(cursor, record)
                self._validate_people(cursor, mapping)
                self._insert(cursor, mapping)
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return mapping

    def resolve_case(
        self,
        *,
        identity_fingerprint: str,
        expected_version: int,
        person_id: str,
        person_version: int,
        evidence_ids: tuple[str, ...],
        authority_ref: str,
        effective_from: datetime,
    ) -> IdentityMapping:
        """Resolve ambiguity to one Person with explicit authority and current version evidence."""
        if not authority_ref or not evidence_ids:
            raise ValueError("case resolution requires authority and evidence")
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"identity:{self._tenant_id}:{identity_fingerprint}",),
                )
                cursor.execute(
                    """
                    SELECT tenant_id, identity_fingerprint, mapping_id, version,
                        identity_kind, normalized_identity, provider_account_ref, purpose,
                        resolution_basis, resolution_authority_ref, outcome, person_id,
                        person_version, resolution_case_id, candidate_person_ids,
                        evidence_ids, effective_from
                    FROM external_identity_mappings_current
                    WHERE tenant_id = %s AND identity_fingerprint = %s FOR UPDATE
                    """.strip(),
                    (self._tenant_id, identity_fingerprint),
                )
                row = cursor.fetchone()
                if row is None:
                    raise KeyError("identity mapping does not exist")
                current = self._row_to_mapping(row)
                if current.version != expected_version or current.outcome not in {
                    "ambiguous",
                    "conflict",
                }:
                    raise ValueError("identity mapping is not the expected unresolved case")
                resolved = IdentityMapping(
                    tenant_id=current.tenant_id,
                    identity_fingerprint=current.identity_fingerprint,
                    mapping_id=current.mapping_id,
                    version=current.version + 1,
                    identity_kind=current.identity_kind,
                    normalized_identity=current.normalized_identity,
                    provider_account_ref=current.provider_account_ref,
                    purpose=current.purpose,
                    resolution_basis="external_mapping",
                    resolution_authority_ref=authority_ref,
                    outcome="matched",
                    person_id=person_id,
                    person_version=person_version,
                    resolution_case_id=None,
                    candidate_person_ids=(),
                    evidence_ids=evidence_ids,
                    effective_from=effective_from.astimezone(UTC),
                )
                self._validate_people(cursor, resolved)
                self._insert_version(cursor, resolved)
                cursor.execute(
                    """
                    UPDATE external_identity_mappings_current SET
                        version = %s, resolution_basis = %s, resolution_authority_ref = %s,
                        outcome = %s, person_id = %s, person_version = %s,
                        resolution_case_id = NULL, candidate_person_ids = '[]'::jsonb,
                        evidence_ids = %s, effective_from = %s, updated_at = clock_timestamp()
                    WHERE tenant_id = %s AND identity_fingerprint = %s
                    """.strip(),
                    (
                        resolved.version,
                        resolved.resolution_basis,
                        resolved.resolution_authority_ref,
                        resolved.outcome,
                        resolved.person_id,
                        resolved.person_version,
                        Jsonb(resolved.evidence_ids),
                        resolved.effective_from,
                        self._tenant_id,
                        resolved.identity_fingerprint,
                    ),
                )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return resolved

    def _validate_people(self, cursor: Cursor, mapping: IdentityMapping) -> None:
        expected: dict[str, int | None] = {}
        if mapping.person_id is not None:
            expected[mapping.person_id] = mapping.person_version
        expected.update(dict.fromkeys(mapping.candidate_person_ids))
        for person_id, expected_version in expected.items():
            cursor.execute(
                """
                SELECT version, record_type FROM canonical_records_current
                WHERE tenant_id = %s AND record_id = %s FOR SHARE
                """.strip(),
                (self._tenant_id, person_id),
            )
            row = cursor.fetchone()
            if row is None or row[1] != "Person":
                raise ValueError(
                    f"identity candidate is not a current canonical Person: {person_id}"
                )
            if expected_version is not None and int(cast(int, row[0])) != expected_version:
                raise ValueError(f"identity Person version changed: {person_id}")

    def _insert(self, cursor: Cursor, mapping: IdentityMapping) -> None:
        self._insert_version(cursor, mapping)
        cursor.execute(
            """
            INSERT INTO external_identity_mappings_current (
                tenant_id, identity_fingerprint, mapping_id, version, identity_kind,
                normalized_identity, provider_account_ref, purpose, resolution_basis,
                resolution_authority_ref, outcome, person_id, person_version,
                resolution_case_id, candidate_person_ids, evidence_ids, effective_from
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """.strip(),
            (
                mapping.tenant_id,
                mapping.identity_fingerprint,
                mapping.mapping_id,
                mapping.version,
                mapping.identity_kind,
                mapping.normalized_identity,
                mapping.provider_account_ref,
                mapping.purpose,
                mapping.resolution_basis,
                mapping.resolution_authority_ref,
                mapping.outcome,
                mapping.person_id,
                mapping.person_version,
                mapping.resolution_case_id,
                Jsonb(mapping.candidate_person_ids),
                Jsonb(mapping.evidence_ids),
                mapping.effective_from,
            ),
        )
        if mapping.outcome in {"ambiguous", "conflict"}:
            cursor.execute(
                """
                INSERT INTO identity_resolution_cases (
                    tenant_id, resolution_case_id, identity_fingerprint, outcome,
                    candidate_person_ids, evidence_ids, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """.strip(),
                (
                    mapping.tenant_id,
                    mapping.resolution_case_id,
                    mapping.identity_fingerprint,
                    mapping.outcome,
                    Jsonb(mapping.candidate_person_ids),
                    Jsonb(mapping.evidence_ids),
                    mapping.effective_from,
                ),
            )

    def _insert_version(self, cursor: Cursor, mapping: IdentityMapping) -> None:
        payload = {
            "tenantId": mapping.tenant_id,
            "identityFingerprint": mapping.identity_fingerprint,
            "mappingId": mapping.mapping_id,
            "version": mapping.version,
            "identityKind": mapping.identity_kind,
            "normalizedIdentity": mapping.normalized_identity,
            "providerAccountRef": mapping.provider_account_ref,
            "purpose": mapping.purpose,
            "resolutionBasis": mapping.resolution_basis,
            "resolutionAuthorityRef": mapping.resolution_authority_ref,
            "outcome": mapping.outcome,
            "personId": mapping.person_id,
            "personVersion": mapping.person_version,
            "resolutionCaseId": mapping.resolution_case_id,
            "candidatePersonIds": mapping.candidate_person_ids,
            "evidenceIds": mapping.evidence_ids,
            "effectiveFrom": mapping.effective_from.isoformat(),
        }
        cursor.execute(
            """
            INSERT INTO external_identity_mapping_versions (
                tenant_id, identity_fingerprint, mapping_id, version, mapping
            ) VALUES (%s, %s, %s, %s, %s)
            """.strip(),
            (
                mapping.tenant_id,
                mapping.identity_fingerprint,
                mapping.mapping_id,
                mapping.version,
                Jsonb(payload),
            ),
        )

    @staticmethod
    def _row_to_mapping(row: tuple[object, ...]) -> IdentityMapping:
        return IdentityMapping(
            tenant_id=str(row[0]),
            identity_fingerprint=str(row[1]),
            mapping_id=str(row[2]),
            version=int(cast(int, row[3])),
            identity_kind=cast(IdentityKind, row[4]),
            normalized_identity=str(row[5]),
            provider_account_ref=None if row[6] is None else str(row[6]),
            purpose=str(row[7]),
            resolution_basis=cast(ResolutionBasis, row[8]),
            resolution_authority_ref=None if row[9] is None else str(row[9]),
            outcome=cast(ResolutionOutcome, row[10]),
            person_id=None if row[11] is None else str(row[11]),
            person_version=None if row[12] is None else int(cast(int, row[12])),
            resolution_case_id=None if row[13] is None else str(row[13]),
            candidate_person_ids=tuple(cast(list[str], row[14])),
            evidence_ids=tuple(cast(list[str], row[15])),
            effective_from=cast(datetime, row[16]),
        )

    def _set_tenant(self, cursor: Cursor) -> None:
        cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
