from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from buyer_ops_contracts.canonical_repository import (
    CanonicalRepository,
    TenantIsolationViolation,
    VersionConflict,
)
from buyer_ops_contracts.errors import ContractViolation


def _agreement(tenant_id: str = "tenant-1", *, version: int = 1) -> dict[str, Any]:
    return {
        "id": "agreement-1",
        "tenantId": tenant_id,
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "WrittenBuyerAgreement",
        "version": version,
        "createdAt": "2029-01-01T00:00:00Z",
        "updatedAt": "2029-01-01T00:00:00Z",
        "effectiveFrom": "2029-01-01T00:00:00Z",
        "createdBy": {"actorType": "system_migration", "actorId": "migration-1"},
        "sourceEvidenceIds": ["evidence-1"],
        "status": "active",
        "agreementType": "non_representation_showing",
        "jurisdiction": "TX",
        "brokerPartyId": "broker-1",
        "responsibleLicenseHolderId": "agent-1",
        "buyerPartyIds": ["party-1"],
        "serviceDefinitions": [{"serviceCode": "showing_access", "allowed": True}],
        "exclusivity": "non_exclusive",
        "effectiveAt": "2029-01-01T00:00:00Z",
        "terminatesAt": "2029-01-15T00:00:00Z",
        "compensation": {
            "determinationMethod": "none for showing-only access",
            "objectivelyAscertainable": True,
            "negotiabilityDisclosurePresent": True,
        },
        "signatureEvidence": [
            {
                "signerPartyId": "party-1",
                "signedAt": "2029-01-01T00:00:00Z",
                "evidenceId": "evidence-1",
            }
        ],
        "executedArtifactId": "artifact-1",
        "executedArtifactDigest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "executionState": "effective",
    }


def _representation_agreement() -> dict[str, Any]:
    agreement = _agreement()
    agreement["agreementType"] = "representation"
    agreement["serviceDefinitions"] = [{"serviceCode": "buyer_representation", "allowed": True}]
    agreement["exclusivity"] = "exclusive"
    return agreement


def _relationship() -> dict[str, Any]:
    return {
        "id": "relationship-1",
        "tenantId": "tenant-1",
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "RepresentationRelationship",
        "version": 1,
        "createdAt": "2029-01-02T00:00:00Z",
        "updatedAt": "2029-01-02T00:00:00Z",
        "effectiveFrom": "2029-01-02T00:00:00Z",
        "createdBy": {"actorType": "service_principal", "actorId": "crm-1"},
        "sourceEvidenceIds": ["evidence-1"],
        "status": "active",
        "brokerageId": "broker-1",
        "buyingPartyId": "party-1",
        "agreementId": "agreement-1",
        "relationshipState": "active",
    }


class Cursor:
    def __init__(self, current_version: int | None = None) -> None:
        self.current_version = current_version
        self.references = {
            "broker-1": {"recordType": "Brokerage", "tenantId": "tenant-1"},
            "agent-1": {"recordType": "LicenseHolder", "tenantId": "tenant-1"},
            "party-1": {"recordType": "BuyingParty", "tenantId": "tenant-1"},
            "artifact-1": {
                "recordType": "DocumentArtifact",
                "tenantId": "tenant-1",
                "digest": "sha256:" + ("a" * 64),
            },
            "evidence-1": {"recordType": "Evidence", "tenantId": "tenant-1"},
            "crm-1": {"recordType": "ServicePrincipal", "tenantId": "tenant-1"},
            "agreement-1": _representation_agreement(),
        }
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self._fetch_value: tuple[object, ...] | None = None

    def __enter__(self) -> Cursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        self.statements.append((statement, parameters))
        if "SELECT version, record" in statement:
            self._fetch_value = (
                (self.current_version, _agreement(version=self.current_version))
                if self.current_version is not None
                else None
            )
        elif "SELECT record FROM canonical_records_current" in statement:
            record = self.references.get(str(parameters[1]))
            self._fetch_value = None if record is None else (record,)
        elif "SELECT record_id FROM canonical_records_current" in statement:
            self._fetch_value = None

    def fetchone(self) -> tuple[object, ...] | None:
        return self._fetch_value

    def fetchall(self) -> list[tuple[object, ...]]:
        return []


class Connection:
    def __init__(self, current_version: int | None = None) -> None:
        self.cursor_instance = Cursor(current_version)
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield

    def cursor(self) -> Cursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        return None


def test_save_requires_tenant_scope_and_advances_version() -> None:
    connection = Connection(current_version=1)
    repository = CanonicalRepository(connection, tenant_id="tenant-1")

    saved = repository.save(_agreement(version=2), expected_version=1)

    assert saved["version"] == 2
    assert connection.commits == 1
    assert any(
        "canonical_record_versions" in sql for sql, _ in connection.cursor_instance.statements
    )


def test_save_locks_record_identity_before_current_version_lookup() -> None:
    connection = Connection()

    CanonicalRepository(connection, tenant_id="tenant-1").save(_agreement())

    statements = connection.cursor_instance.statements
    assert "set_config" in statements[0][0]
    assert "pg_advisory_xact_lock" in statements[1][0]
    assert statements[1][1] == ("canonical-record:tenant-1:agreement-1",)
    assert "SELECT version, record" in statements[2][0]


def test_save_rejects_cross_tenant_record_before_database_write() -> None:
    connection = Connection()
    repository = CanonicalRepository(connection, tenant_id="tenant-1")

    with pytest.raises(TenantIsolationViolation):
        repository.save(_agreement(tenant_id="tenant-2"))

    assert connection.cursor_instance.statements == []
    assert connection.commits == 0


def test_save_rejects_stale_expected_version() -> None:
    connection = Connection(current_version=3)
    repository = CanonicalRepository(connection, tenant_id="tenant-1")

    with pytest.raises(VersionConflict):
        repository.save(_agreement(version=4), expected_version=2)

    assert not any(
        "canonical_record_versions" in sql for sql, _ in connection.cursor_instance.statements
    )


def test_save_runs_semantic_admission_before_database_write() -> None:
    connection = Connection()
    repository = CanonicalRepository(connection, tenant_id="tenant-1")
    record = _agreement()
    record["sourceEvidenceIds"] = []

    with pytest.raises(ContractViolation, match="SOURCE_EVIDENCE_REQUIRED"):
        repository.save(record)

    assert connection.cursor_instance.statements == []


def test_active_representation_cardinality_uses_narrow_serialization_key() -> None:
    connection = Connection()
    CanonicalRepository(connection, tenant_id="tenant-1").save(_relationship())

    assert any(
        "pg_advisory_xact_lock" in sql and parameters == ("active-representation:tenant-1:party-1",)
        for sql, parameters in connection.cursor_instance.statements
    )


def test_save_rejects_executed_artifact_digest_mismatch() -> None:
    connection = Connection()
    agreement = _agreement()
    agreement["executedArtifactDigest"] = "sha256:" + ("b" * 64)

    with pytest.raises(ContractViolation, match="ARTIFACT_DIGEST_MISMATCH"):
        CanonicalRepository(connection, tenant_id="tenant-1").save(agreement)


def _agent_person() -> dict[str, Any]:
    return {
        "id": "person-agent-1",
        "tenantId": "tenant-1",
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "Person",
        "version": 1,
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
        "effectiveFrom": "2026-01-01T00:00:00Z",
        "createdBy": {"actorType": "system_migration", "actorId": "migration-1"},
        "sourceEvidenceIds": ["evidence-1"],
        "status": "active",
        "identityState": "resolved",
        "displayName": "Sponsored Agent",
        "endpointIds": ["endpoint-agent-1"],
    }


def _license_holder() -> dict[str, Any]:
    return {
        "id": "holder-1",
        "tenantId": "tenant-1",
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "LicenseHolder",
        "version": 1,
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
        "effectiveFrom": "2026-01-01T00:00:00Z",
        "createdBy": {"actorType": "system_migration", "actorId": "migration-1"},
        "sourceEvidenceIds": ["evidence-1"],
        "status": "active",
        "personId": "person-agent-1",
        "licenseNumber": "1234567",
        "licenseType": "sales_agent",
        "jurisdiction": "TX",
        "activeFrom": "2026-01-01T00:00:00Z",
        "licenseState": "active",
    }


def test_save_admits_active_license_holder_with_current_person_and_evidence() -> None:
    connection = Connection()
    connection.cursor_instance.references["person-agent-1"] = {
        "recordType": "Person",
        "tenantId": "tenant-1",
    }
    connection.cursor_instance.references["endpoint-agent-1"] = {
        "recordType": "ContactEndpoint",
        "tenantId": "tenant-1",
    }
    saved = CanonicalRepository(connection, tenant_id="tenant-1").save(_license_holder())
    assert saved["recordType"] == "LicenseHolder"
    assert saved["licenseState"] == "active"
    assert saved["personId"] == "person-agent-1"
    assert connection.commits == 1


def test_save_rejects_license_holder_without_current_person() -> None:
    connection = Connection()
    with pytest.raises(ContractViolation, match="REFERENCE_NOT_FOUND"):
        CanonicalRepository(connection, tenant_id="tenant-1").save(_license_holder())
