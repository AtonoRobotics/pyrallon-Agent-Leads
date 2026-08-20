from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg.types.json import Jsonb

from buyer_ops_contracts.acknowledgment_repository import AcknowledgmentRepository
from buyer_ops_contracts.activation import ActivationController, evidence_set_digest
from buyer_ops_contracts.actor_authorization import ActorTenantAuthorizationRepository
from buyer_ops_contracts.artifacts import ArtifactPointer
from buyer_ops_contracts.audit import verify_tenant_export
from buyer_ops_contracts.canonical_repository import CanonicalRepository, VersionConflict
from buyer_ops_contracts.closure_repository import PostgresClosureRepository
from buyer_ops_contracts.contract_acceptance import (
    ContractSemanticError,
    require_unknown_outcome_resolution,
)
from buyer_ops_contracts.derived_contract_repository import (
    BookingOutcomeRepository,
    DerivedContractReader,
    QualificationDecisionPairRepository,
    SlotSetRepository,
)
from buyer_ops_contracts.errors import ContractViolation
from buyer_ops_contracts.evidence import EvidenceIntegrityError, verify_checkpoint
from buyer_ops_contracts.evidence_lifecycle import (
    ArtifactRepository,
    DeletionPropagationRepository,
)
from buyer_ops_contracts.evidence_repository import DeletionDenied, EvidenceRepository
from buyer_ops_contracts.habitat import HabitatKernel, HabitatState, PolicyDisposition
from buyer_ops_contracts.habitat_repository import (
    PostgresHabitatRepository,
    PostgresVersionLockedStateReader,
)
from buyer_ops_contracts.identity import IdentityRepository
from buyer_ops_contracts.ingress import (
    InboundEnvelope,
    IngressRejected,
    PostgresInboundEventRegistry,
)
from buyer_ops_contracts.ingress_service import IngressService
from buyer_ops_contracts.operator_commands import (
    OperatorCommandService as CanonicalOperatorCommandService,
)
from buyer_ops_contracts.operator_commands import (
    command_payload_digest,
)
from buyer_ops_contracts.operator_policy import OperatorPolicyRepository
from buyer_ops_contracts.operator_surface import (
    OperatorRejected,
    PostgresOperatorIdempotencyRepository,
)
from buyer_ops_contracts.release_activation_v1 import ReleaseActivationRepository
from buyer_ops_contracts.release_evidence import ReleaseEvidenceEvaluator, load_gate_registry
from buyer_ops_contracts.retention import RetentionConfiguration, RetentionPolicy
from buyer_ops_contracts.telemetry import TelemetryRecorder, load_metric_catalog

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[1]
CATALOG_FIXTURES = json.loads(
    (ROOT / "tests" / "fixtures" / "generated" / "ontology_0_3_valid.json").read_text()
)
MIGRATION = ROOT / "migrations" / "0001_canonical_records.sql"
ROLLBACK = ROOT / "migrations" / "0001_canonical_records.rollback.sql"
EVIDENCE_MIGRATION = ROOT / "migrations" / "0002_evidence_ledger.sql"
EVIDENCE_ROLLBACK = ROOT / "migrations" / "0002_evidence_ledger.rollback.sql"
IDENTITY_MIGRATION = ROOT / "migrations" / "0003_identity_resolution.sql"
IDENTITY_ROLLBACK = ROOT / "migrations" / "0003_identity_resolution.rollback.sql"
ONTOLOGY_02_MIGRATION = ROOT / "migrations" / "0004_ontology_0_2.sql"
HABITAT_MIGRATION = ROOT / "migrations" / "0005_habitat_permits.sql"
ONTOLOGY_03_MIGRATION = ROOT / "migrations" / "0006_ontology_0_3.sql"
INGRESS_MIGRATION = ROOT / "migrations" / "0007_inbound_events.sql"
INGRESS_ROLLBACK = ROOT / "migrations" / "0007_inbound_events.rollback.sql"
OPERATOR_MIGRATION = ROOT / "migrations" / "0008_operator_commands.sql"
OPERATOR_ROLLBACK = ROOT / "migrations" / "0008_operator_commands.rollback.sql"
CONTROL_PLANE_MIGRATION = ROOT / "migrations" / "0009_control_plane.sql"
CONTROL_PLANE_ROLLBACK = ROOT / "migrations" / "0009_control_plane.rollback.sql"
OPERATOR_11_MIGRATION = ROOT / "migrations" / "0011_operator_surface_1_1.sql"
ACKNOWLEDGMENT_MIGRATION = ROOT / "migrations" / "0012_ot01_acknowledgment.sql"
ACTOR_AUTHORIZATION_MIGRATION = ROOT / "migrations" / "0013_actor_tenant_authorization.sql"
RELEASE_ACTIVATION_MIGRATION = ROOT / "migrations" / "0014_release_activation.sql"
RELEASE_ACTIVATION_CONCURRENCY_MIGRATION = (
    ROOT / "migrations" / "0015_release_activation_concurrency.sql"
)
RELEASE_ACTIVATION_CONCURRENCY_ROLLBACK = (
    ROOT / "migrations" / "0015_release_activation_concurrency.rollback.sql"
)
DERIVED_CONTRACT_MIGRATION = ROOT / "migrations" / "0021_derived_contract_records.sql"
DERIVED_CONTRACT_ROLLBACK = ROOT / "migrations" / "0021_derived_contract_records.rollback.sql"
DSN = os.environ.get("BUYER_OPS_TEST_POSTGRES_DSN")


def _agreement(tenant_id: str, *, record_id: str, version: int) -> dict[str, Any]:
    return {
        "id": record_id,
        "tenantId": tenant_id,
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "WrittenBuyerAgreement",
        "version": version,
        "createdAt": "2029-01-01T00:00:00Z",
        "updatedAt": f"2029-01-{version:02d}T00:00:00Z",
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
        "executedArtifactDigest": (
            "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        ),
        "executionState": "effective",
    }


def _relationship(*, agreement_id: str, record_id: str) -> dict[str, Any]:
    return {
        "id": record_id,
        "tenantId": "tenant-a",
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
        "agreementId": agreement_id,
        "relationshipState": "active",
    }


def _person(record_id: str) -> dict[str, Any]:
    return {
        "id": record_id,
        "tenantId": "tenant-a",
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": "Person",
        "version": 1,
        "createdAt": "2029-01-01T00:00:00Z",
        "updatedAt": "2029-01-01T00:00:00Z",
        "effectiveFrom": "2029-01-01T00:00:00Z",
        "createdBy": {"actorType": "service_principal", "actorId": "crm-1"},
        "sourceEvidenceIds": ["evidence-1"],
        "status": "active",
        "identityState": "resolved",
        "displayName": "Buyer",
        "endpoints": [
            {
                "endpointId": "endpoint-1",
                "type": "email",
                "normalizedValue": "buyer@example.com",
                "verificationState": "verified",
                "status": "active",
            }
        ],
    }


@pytest.fixture(scope="module")
def postgres_dsn() -> str:
    if DSN is None:
        pytest.skip("BUYER_OPS_TEST_POSTGRES_DSN is not configured")
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(MIGRATION.read_text())
        admin.execute(ROLLBACK.read_text())
        assert admin.execute("SELECT to_regclass('canonical_records_current')").fetchone() == (
            None,
        )
        admin.execute(MIGRATION.read_text())
        admin.execute(ONTOLOGY_02_MIGRATION.read_text())
        admin.execute(EVIDENCE_MIGRATION.read_text())
        admin.execute(EVIDENCE_ROLLBACK.read_text())
        assert admin.execute("SELECT to_regclass('evidence_ledger')").fetchone() == (None,)
        admin.execute(EVIDENCE_MIGRATION.read_text())
        admin.execute(IDENTITY_MIGRATION.read_text())
        admin.execute(HABITAT_MIGRATION.read_text())
        admin.execute(ONTOLOGY_03_MIGRATION.read_text())
        admin.execute(INGRESS_MIGRATION.read_text())
        admin.execute(OPERATOR_MIGRATION.read_text())
        admin.execute(CONTROL_PLANE_MIGRATION.read_text())
        admin.execute(CONTROL_PLANE_MIGRATION.read_text())
        admin.execute(CONTROL_PLANE_ROLLBACK.read_text())
        assert admin.execute("SELECT to_regclass('closure_records')").fetchone() == (None,)
        admin.execute(CONTROL_PLANE_MIGRATION.read_text())
        admin.execute(OPERATOR_ROLLBACK.read_text())
        assert admin.execute("SELECT to_regclass('operator_command_results')").fetchone() == (None,)
        admin.execute(OPERATOR_MIGRATION.read_text())
        admin.execute(CONTROL_PLANE_MIGRATION.read_text())
        admin.execute(INGRESS_ROLLBACK.read_text())
        assert admin.execute("SELECT to_regclass('inbound_events')").fetchone() == (None,)
        admin.execute(INGRESS_MIGRATION.read_text())
        admin.execute(CONTROL_PLANE_MIGRATION.read_text())
        admin.execute(IDENTITY_ROLLBACK.read_text())
        assert admin.execute("SELECT to_regclass('identity_resolution_cases')").fetchone() == (
            None,
        )
        admin.execute(IDENTITY_MIGRATION.read_text())
        admin.execute(OPERATOR_11_MIGRATION.read_text())
        admin.execute(ACKNOWLEDGMENT_MIGRATION.read_text())
        admin.execute(ACTOR_AUTHORIZATION_MIGRATION.read_text())
        admin.execute(RELEASE_ACTIVATION_MIGRATION.read_text())
        admin.execute(RELEASE_ACTIVATION_CONCURRENCY_MIGRATION.read_text())
        admin.execute(DERIVED_CONTRACT_MIGRATION.read_text())
        admin.execute(DERIVED_CONTRACT_ROLLBACK.read_text())
        assert admin.execute("SELECT to_regclass('derived_contract_records')").fetchone() == (None,)
        admin.execute(DERIVED_CONTRACT_MIGRATION.read_text())
        admin.execute(
            "DO $$ BEGIN "
            "CREATE ROLE buyer_ops_test_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS; "
            "EXCEPTION WHEN duplicate_object THEN NULL; "
            "END $$"
        )
        admin.execute("ALTER ROLE buyer_ops_test_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS")
        admin.execute("GRANT USAGE ON SCHEMA public TO buyer_ops_test_runtime")
        admin.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON "
            "canonical_records_current, canonical_record_versions, "
            "evidence_artifact_versions, evidence_ledger, evidence_checkpoints, "
            "evidence_legal_hold_events, projection_fences, evidence_deletion_tombstones, "
            "derived_invalidation_events, external_identity_mappings_current, "
            "external_identity_mapping_versions, identity_resolution_cases "
            ", habitat_authority_decisions, habitat_effect_permits "
            ", inbound_events "
            ", operator_command_results "
            ", operator_policy_versions, operator_policies_current "
            ", ingress_ack_config_versions, ingress_ack_configs_current, "
            "ingress_acknowledgment_decisions, ingress_acknowledgment_outcomes "
            ", inbound_message_conflicts, closure_records, closure_records_current, telemetry_observations, "
            "release_gate_evidence, release_activation_decisions, ingress_attribution, "
            "ingress_consent_presentation, operator_actor_tenancies, "
            "actor_tenant_authorization_versions, actor_tenant_authorizations_current, "
            "release_activation_versions, derived_contract_records "
            "TO buyer_ops_test_runtime"
        )
    _seed_reference_graph(DSN)
    return DSN


def _runtime_connection(dsn: str) -> psycopg.Connection[Any]:
    connection = psycopg.connect(dsn)
    connection.execute("SET ROLE buyer_ops_test_runtime")
    connection.commit()
    return connection


class _AcceptActivationSignature:
    def verify(self, activation: dict[str, Any]) -> bool:
        return activation["signature"] == "sha256:" + "d" * 64


class _AcceptActivationSigner:
    def verify(self, activation: dict[str, Any], *, evaluated_at: datetime) -> bool:
        return activation["signerActorId"] == "release-manager"


def test_open025_026_append_only_persistence_and_readback(postgres_dsn: str) -> None:
    now = datetime(2030, 1, 2, tzinfo=UTC)
    grant = {
        "schemaVersion": "open-025-027/1.0.0",
        "recordType": "ActorTenantAuthorization",
        "tenantId": "tenant-a",
        "recordId": "actor-grant-pg-1",
        "observedAt": "2030-01-01T00:00:00Z",
        "actorId": "operator-pg-1",
        "principalId": "principal-pg-1",
        "role": "release_manager",
        "allowedCommands": ["activate_release"],
        "recordScopes": ["release-pg-1"],
        "policyVersion": "policy-pg-1",
        "authorizationVersion": 1,
        "effectiveAt": "2030-01-01T00:00:00Z",
        "expiresAt": "2030-02-01T00:00:00Z",
        "status": "active",
    }
    connection = _runtime_connection(postgres_dsn)
    try:
        ActorTenantAuthorizationRepository(connection, tenant_id="tenant-a").save(grant, now=now)
        assert (
            ActorTenantAuthorizationRepository(connection, tenant_id="tenant-a").current(
                "operator-pg-1", now=now
            )
            == grant
        )
        activation = {
            "schemaVersion": "open-025-027/1.0.0",
            "recordType": "ReleaseActivation",
            "tenantId": "tenant-a",
            "recordId": "activation-pg-1",
            "observedAt": "2030-01-02T00:00:00Z",
            "environment": "staging",
            "releaseId": "release-pg-1",
            "buildDigest": "sha256:" + "a" * 64,
            "contractManifestDigest": "sha256:" + "b" * 64,
            "policyVersion": "policy-pg-1",
            "enabledCapabilities": ["connector-pg-1:send"],
            "requiredGateIds": ["GATE-001"],
            "gateEvidence": [
                {
                    "gateId": "GATE-001",
                    "applicability": "platform_invariant",
                    "outcome": "pass",
                    "evidenceId": "evidence-pg-1",
                    "evidenceDigest": "sha256:" + "c" * 64,
                    "expiresAt": "2030-02-01T00:00:00Z",
                }
            ],
            "signerActorId": "release-manager",
            "signature": "sha256:" + "d" * 64,
            "effectiveAt": "2030-01-01T00:00:00Z",
            "expiresAt": "2030-02-01T00:00:00Z",
            "status": "active",
        }
        activations = ReleaseActivationRepository(
            connection,
            tenant_id="tenant-a",
            signature_verifier=_AcceptActivationSignature(),
            signer_authority=_AcceptActivationSigner(),
        )
        activations.admit(activation, evaluated_at=now)
        assert activations.readback("activation-pg-1", evaluated_at=now) == (
            activation,
            True,
        )
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', true)")
        with pytest.raises(psycopg.DatabaseError, match="append-only"):
            connection.execute(
                "UPDATE release_activation_versions SET status='revoked' "
                "WHERE record_id='activation-pg-1'"
            )
        connection.rollback()
    finally:
        connection.close()


def _inbound_envelope(
    provider_event_id: str = "provider-event-1", *, digest: str | None = None
) -> InboundEnvelope:
    return InboundEnvelope.from_mapping(
        {
            "schemaVersion": "ot01.inbound/1",
            "providerEventId": provider_event_id,
            "providerAccountRef": "provider-account-1",
            "channel": "sms",
            "receivedAt": "2029-01-01T00:00:00Z",
            "senderEndpoint": "+15551234567",
            "recipientEndpoint": "+15557654321",
            "payloadArtifactId": "artifact-1",
            "payloadDigest": digest or "sha256:" + "a" * 64,
            "signatureVerification": "verified",
        }
    )


def _external_message_identity(
    external_event_id: str = "provider-event-1", *, digest: str | None = None
) -> dict[str, Any]:
    return {
        "schemaVersion": "open-019-024/1.1.0",
        "tenantId": "tenant-a",
        "recordId": f"message-identity-{external_event_id}",
        "recordVersion": 1,
        "observedAt": "2029-01-01T00:00:00Z",
        "effectiveFrom": "2029-01-01T00:00:00Z",
        "status": "current",
        "evidenceRefs": ["provider-envelope-1"],
        "recordType": "ExternalMessageIdentity",
        "connectorId": "connector-1",
        "provider": "provider-1",
        "providerAccountRef": "provider-account-1",
        "externalMessageId": "provider-message-1",
        "externalEventId": external_event_id,
        "payloadDigest": digest or "sha256:" + "a" * 64,
    }


def _capability_inventory(*, record_id: str, version: int) -> dict[str, Any]:
    record = {
        "schemaVersion": "open-019-024/1.1.0",
        "tenantId": "tenant-a",
        "recordId": record_id,
        "recordVersion": version,
        "observedAt": f"2029-01-0{version}T00:00:00Z",
        "effectiveFrom": f"2029-01-0{version}T00:00:00Z",
        "expiresAt": "2030-01-01T00:00:00Z",
        "status": "current",
        "evidenceRefs": [f"connector-attestation-{version}"],
        "recordType": "CapabilityInventory",
        "connectorId": "connector-1",
        "connectorVersion": f"{version}.0.0",
        "capabilities": ["read", "send"],
        "effectClasses": ["send_message"],
        "capabilityEffects": [
            {
                "capability": "send",
                "actionClasses": ["send_message"],
                "constraintDigest": "sha256:" + "3" * 64,
            }
        ],
        "canonicalizationVersion": "jcs-rfc8785/1",
        "inventoryDigest": "sha256:" + str(version) * 64,
        "signature": {
            "algorithm": "Ed25519",
            "keyId": "connector-key-1",
            "value": "signature",
        },
    }
    if version > 1:
        record["supersedesRecordId"] = "inventory-1"
    return record


def test_real_postgres_registers_exact_inbound_replays_once(postgres_dsn: str) -> None:
    with _runtime_connection(postgres_dsn) as connection:
        registry = PostgresInboundEventRegistry(connection)
        first = registry.register("tenant-a", _inbound_envelope(), _external_message_identity())
        replay = registry.register(
            "tenant-a",
            _inbound_envelope("provider-event-2"),
            _external_message_identity("provider-event-2"),
        )
        assert first.duplicate is False
        assert replay == replace(first, duplicate=True, duplicate_of=first.event_id)
        connection.execute("SET LOCAL app.tenant_id = 'tenant-a'")
        count = connection.execute(
            "SELECT count(*) FROM inbound_events WHERE provider_event_id = %s",
            ("provider-event-1",),
        ).fetchone()
        assert count == (1,)
        with pytest.raises(IngressRejected, match="reconciliation_required"):
            conflicting_digest = "sha256:" + "b" * 64
            registry.register(
                "tenant-a",
                _inbound_envelope("provider-event-3", digest=conflicting_digest),
                _external_message_identity("provider-event-3", digest=conflicting_digest),
            )


def test_attribution_admission_matches_migrated_storage_and_replays_exactly(
    postgres_dsn: str,
) -> None:
    attribution = _attribution_input_fixture(tenant_id="tenant-a", suffix="storage")
    with _runtime_connection(postgres_dsn) as connection:
        service = IngressService(connection, tenant_id="tenant-a")
        assert service.admit(attribution) == attribution
        assert service.admit(copy.deepcopy(attribution)) == attribution

        conflicting_digest = copy.deepcopy(attribution)
        conflicting_digest["payloadDigest"] = "sha256:" + "b" * 64
        with pytest.raises(ValueError, match="duplicate attribution"):
            service.admit(conflicting_digest)

        conflicting_metadata = copy.deepcopy(attribution)
        conflicting_metadata["sourceInstanceId"] = "source-conflict"
        with pytest.raises(ValueError, match="duplicate attribution"):
            service.admit(conflicting_metadata)

        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        assert connection.execute(
            "SELECT payload, recorded_at IS NOT NULL FROM ingress_attribution "
            "WHERE attribution_id = %s",
            (attribution["attributionId"],),
        ).fetchone() == (attribution, True)


def test_consent_presentation_replay_is_exact_append_only_and_tenant_scoped(
    postgres_dsn: str,
) -> None:
    original = _consent_presentation_fixture(tenant_id="tenant-a", suffix="replay")
    with _runtime_connection(postgres_dsn) as connection:
        service = IngressService(connection, tenant_id="tenant-a")
        assert service.admit(original) == original
        assert service.admit(copy.deepcopy(original)) == original

        conflicting_digest = copy.deepcopy(original)
        conflicting_digest["payloadDigest"] = "sha256:" + "b" * 64
        with pytest.raises(ValueError, match="duplicate consent presentation"):
            service.admit(conflicting_digest)

        conflicting_metadata = copy.deepcopy(original)
        conflicting_metadata["disclosureVersion"] = "2.0.0"
        with pytest.raises(ValueError, match="duplicate consent presentation"):
            service.admit(conflicting_metadata)

        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        assert connection.execute(
            "SELECT payload FROM ingress_consent_presentation WHERE evidence_id = %s",
            (original["evidenceId"],),
        ).fetchone() == (original,)

    tenant_b = copy.deepcopy(original)
    tenant_b["tenantId"] = "tenant-b"
    tenant_b["subjectPersonId"] = "person-tenant-b"
    with _runtime_connection(postgres_dsn) as connection:
        assert IngressService(connection, tenant_id="tenant-b").admit(tenant_b) == tenant_b
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-b', false)")
        assert connection.execute(
            "SELECT payload FROM ingress_consent_presentation WHERE evidence_id = %s",
            (tenant_b["evidenceId"],),
        ).fetchone() == (tenant_b,)


def test_real_postgres_closure_history_and_current_projection(postgres_dsn: str) -> None:
    with _runtime_connection(postgres_dsn) as connection:
        repository = PostgresClosureRepository(connection, tenant_id="tenant-a")
        repository.save(_capability_inventory(record_id="inventory-1", version=1))
        repository.save(_capability_inventory(record_id="inventory-2", version=2))
        current = repository.current_inventory("tenant-a", "connector-1")
        assert current is not None
        assert current["recordId"] == "inventory-2"
        connection.execute("SET LOCAL app.tenant_id = 'tenant-a'")
        assert connection.execute(
            "SELECT count(*) FROM closure_records WHERE record_type = 'CapabilityInventory'"
        ).fetchone() == (2,)
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute(
                "UPDATE closure_records SET status = 'superseded' "
                "WHERE tenant_id = 'tenant-a' AND record_id = 'inventory-1'"
            )


def test_real_postgres_activation_requires_release_and_build_bound_evidence(
    postgres_dsn: str,
) -> None:
    registry, registry_digest = load_gate_registry(ROOT / "PRODUCTION-GATE-REGISTRY.yaml")

    class Disablement:
        def proves_disabled(self, capability_id: str, evidence_refs: list[str]) -> bool:
            return False

    class Signatures:
        def verify(self, decision: dict[str, Any]) -> bool:
            return decision["signature"]["keyId"] == "release-key-1"

    release_digest = "sha256:" + "a" * 64
    build_digest = "sha256:" + "b" * 64
    release = {
        "schemaVersion": "open-019-024/1.1.0",
        "tenantId": "tenant-a",
        "recordId": "release-gate-002",
        "recordVersion": 1,
        "observedAt": "2026-01-01T00:00:00Z",
        "effectiveFrom": "2026-01-01T00:00:00Z",
        "status": "current",
        "evidenceRefs": ["idempotency-fault-run"],
        "recordType": "ReleaseEvidence",
        "gateId": "GATE-002",
        "gateRegistryVersion": registry["registry_version"],
        "gateRegistryDigest": registry_digest,
        "applicability": "platform_invariant",
        "scope": "all_live_effects",
        "releaseDigest": release_digest,
        "testVersion": "fault-suite/1",
        "outcome": "pass",
        "ownerId": "platform-operations",
        "expiresAt": "2030-01-01T00:00:00Z",
    }
    accessibility = {
        "schemaVersion": "open-019-024/1.1.0",
        "tenantId": "tenant-a",
        "recordId": "a11y-web-build",
        "recordVersion": 1,
        "observedAt": "2026-01-01T00:00:00Z",
        "effectiveFrom": "2026-01-01T00:00:00Z",
        "status": "current",
        "evidenceRefs": ["wcag-web-run"],
        "recordType": "AccessibilityEvidence",
        "standard": "WCAG 2.2 AA",
        "suiteVersion": "a11y-suite/1",
        "surface": "web",
        "buildDigest": build_digest,
        "releaseDigest": release_digest,
        "assistiveTechnologies": ["keyboard", "screen-reader"],
        "knownExceptions": [],
        "outcome": "current",
        "ownerId": "accessibility-owner",
        "expiresAt": "2030-01-01T00:00:00Z",
    }
    evidence_ids = [release["recordId"], accessibility["recordId"]]
    decision = {
        "messageType": "activation_decision",
        "schemaVersion": "release-activation/1.1.0",
        "decisionId": "activation-1",
        "capabilityId": "all_external_effects",
        "tenantId": "tenant-a",
        "environment": "staging",
        "releaseDigest": release_digest,
        "gateRegistryVersion": registry["registry_version"],
        "gateRegistryDigest": registry_digest,
        "directlyApplicableGateIds": ["GATE-002"],
        "requiredGateIds": ["GATE-002"],
        "deployedBuildDigests": {"web": build_digest},
        "expectedActivationVersion": 0,
        "decision": "activate",
        "evidenceIds": [release["recordId"]],
        "accessibilityEvidenceIds": [accessibility["recordId"]],
        "evidenceSetDigest": evidence_set_digest(evidence_ids),
        "authorizedBy": "release-manager",
        "authorizationId": "release-authorization-1",
        "decidedAt": "2026-08-19T12:00:00Z",
        "rollbackState": "armed",
        "readbackRequired": True,
        "signature": {
            "keyId": "release-key-1",
            "algorithm": "ed25519",
            "value": "a" * 64,
        },
    }
    with _runtime_connection(postgres_dsn) as connection:
        controller = ActivationController(
            connection,
            tenant_id="tenant-a",
            evaluator=ReleaseEvidenceEvaluator(
                registry, registry_digest, Disablement(), tenant_id="tenant-a"
            ),
            signature_verifier=Signatures(),
        )
        controller.record_gate_evidence(release)
        controller.record_gate_evidence(accessibility)
        assert controller.record_decision(decision) == decision
        assert controller.capability_activated("all_external_effects") is True

    stale = {**decision, "decisionId": "activation-stale"}
    with _runtime_connection(postgres_dsn) as connection:
        stale_controller = ActivationController(
            connection,
            tenant_id="tenant-a",
            evaluator=ReleaseEvidenceEvaluator(
                registry, registry_digest, Disablement(), tenant_id="tenant-a"
            ),
            signature_verifier=Signatures(),
        )
        with pytest.raises(ValueError, match="activation version conflict"):
            stale_controller.record_decision(stale)

    contenders = [
        {
            **decision,
            "decisionId": f"activation-concurrent-{suffix}",
            "expectedActivationVersion": 1,
            "decidedAt": f"2026-08-19T12:00:0{offset}Z",
        }
        for suffix, offset in (("a", 1), ("b", 2))
    ]

    def admit_contender(candidate: dict[str, Any]) -> str:
        with _runtime_connection(postgres_dsn) as connection:
            candidate_controller = ActivationController(
                connection,
                tenant_id="tenant-a",
                evaluator=ReleaseEvidenceEvaluator(
                    registry, registry_digest, Disablement(), tenant_id="tenant-a"
                ),
                signature_verifier=Signatures(),
            )
            try:
                candidate_controller.record_decision(candidate)
            except ValueError as exc:
                assert "activation version conflict" in str(exc)
                return "conflict"
            return str(candidate["decisionId"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(admit_contender, contenders))
    assert outcomes.count("conflict") == 1
    assert len([outcome for outcome in outcomes if outcome != "conflict"]) == 1

    with _runtime_connection(postgres_dsn) as connection:
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        versions = connection.execute(
            "SELECT activation_version FROM release_activation_decisions "
            "WHERE capability_id = 'all_external_effects' ORDER BY activation_version"
        ).fetchall()
        assert versions == [(1,), (2,)]


def test_real_postgres_operator_idempotency_binds_payload_and_result(postgres_dsn: str) -> None:
    result = {
        "message_type": "operator_command_result",
        "schema_version": "operator-surface/1.1.0",
        "command_id": "operator-pg-command-1",
        "tenant_id": "tenant-a",
        "status": "applied",
        "decided_at": "2029-01-01T00:00:00Z",
        "decision_evidence_id": "evidence-1",
        "current_version": 2,
        "result_refs": [],
    }
    digest = "sha256:" + "a" * 64
    with _runtime_connection(postgres_dsn) as connection:
        repository = PostgresOperatorIdempotencyRepository(connection)
        repository.record("tenant-a", "operator-pg-key-1", digest, result)
        assert repository.lookup("tenant-a", "operator-pg-key-1") == (digest, result)
        repository.record("tenant-a", "operator-pg-key-1", digest, result)
        with pytest.raises(OperatorRejected) as raised:
            repository.record("tenant-a", "operator-pg-key-1", "sha256:" + "b" * 64, result)
        assert raised.value.code == "payload_mismatch"


class _HabitatPolicy:
    def evaluate(self, intent, state, evaluated_at):
        return PolicyDisposition("allowed", "tenant-effect-policy", "7")


class _LockedHabitatStateReader:
    def load_current(self, cursor, intent):
        target = intent["target_resource"]
        return HabitatState(
            records={
                "conversation-1": {
                    "id": "conversation-1",
                    "tenantId": "tenant-a",
                    "version": 4,
                },
                "person-1": {"id": "person-1", "tenantId": "tenant-a", "version": 2},
            },
            principal={
                "id": "principal-1",
                "tenantId": "tenant-a",
                "recordType": "ServicePrincipal",
                "status": "active",
                "principalState": "active",
            },
            authorization={
                "status": "active",
                "authorizationState": "active",
                "granteeId": "principal-1",
                "actionClass": "send_message",
                "resourceType": target["resource_type"],
                "resourceId": target["resource_id"],
                "grantedAt": "2029-12-31T00:00:00Z",
                "expiresAt": "2030-01-02T00:00:00Z",
            },
            workflow_reference={
                "status": "active",
                "workflowId": intent["workflow_id"],
                "subjectId": intent["buyer_journey_id"],
                "executionState": "running",
            },
            connector_grant={
                "connectorBindingId": "connector-1",
                "principalId": "principal-1",
                "state": "active",
                "actionClasses": ["send_message"],
                "requiresConsent": False,
            },
        )


class _BlockingHabitatStateReader(_LockedHabitatStateReader):
    def __init__(self, locked: threading.Event, release: threading.Event) -> None:
        self._locked = locked
        self._release = release

    def load_current(self, cursor, intent):
        self._locked.set()
        if not self._release.wait(timeout=5):
            raise TimeoutError("test did not release locked Habitat state read")
        return super().load_current(cursor, intent)


def _habitat_intent() -> dict[str, Any]:
    return {
        "schema_version": "effect-intent/1.0.0",
        "intent_id": "intent-atomic-1",
        "tenant_id": "tenant-a",
        "principal_id": "principal-1",
        "buyer_journey_id": "journey-1",
        "workflow_id": "workflow-1",
        "activity_id": "activity-1",
        "action_class": "send_message",
        "connector_binding_id": "connector-1",
        "target_resource": {
            "resource_type": "conversation",
            "resource_id": "conversation-1",
            "version": 4,
        },
        "recipient": {"recipient_type": "person", "recipient_id": "person-1"},
        "payload_digest": "sha256:" + "a" * 64,
        "canonical_version_vector": {"conversation-1": 4, "person-1": 2},
        "proposal_id": "proposal-1",
        "proposal_expires_at": "2030-01-01T00:05:00Z",
        "idempotency_key": "send:conversation-1:4:atomic",
        "purpose": "buyer_consultation",
        "trace_id": "trace-atomic-1",
        "evidence_correlation_ids": ["evidence-1"],
    }


def _dependency(
    record_type: str, record_id: str, *, tenant_id: str = "tenant-a", **fields: Any
) -> dict[str, Any]:
    record = copy.deepcopy(CATALOG_FIXTURES[record_type])
    record.update(id=record_id, tenantId=tenant_id, **fields)
    record["createdBy"] = {"actorType": "system_migration", "actorId": "fixture-bootstrap"}
    if record_type != "Evidence" and "sourceEvidenceIds" not in fields:
        record["sourceEvidenceIds"] = []
    if record_type == "Person":
        record.pop("endpointIds", None)
        record["endpoints"] = []
    if record_type == "Evidence":
        record.update(sourceType="system_observation", sourceRef="fixture-bootstrap")
    return record


def _seed_reference_graph(dsn: str) -> None:
    for tenant_id in ("tenant-a", "tenant-b"):
        records = [
            _dependency("Person", "value", tenant_id=tenant_id),
            _dependency("ServicePrincipal", "crm-1", tenant_id=tenant_id),
            _dependency("Brokerage", "broker-1", tenant_id=tenant_id),
            _dependency("Person", "agent-person-1", tenant_id=tenant_id),
            _dependency("Person", "buyer-person-1", tenant_id=tenant_id),
            _dependency(
                "LicenseHolder",
                "agent-1",
                tenant_id=tenant_id,
                personId="agent-person-1",
                sponsoringBrokerageId="broker-1",
            ),
            _dependency(
                "BuyingParty",
                "party-1",
                tenant_id=tenant_id,
                members=[{"personId": "buyer-person-1", "role": "buyer"}],
            ),
            _dependency("DocumentArtifact", "artifact-1", tenant_id=tenant_id),
            _dependency("Evidence", "evidence-1", tenant_id=tenant_id),
        ]
        with _runtime_connection(dsn) as connection:
            repository = CanonicalRepository(connection, tenant_id=tenant_id)
            for record in records:
                repository.save(record)


def test_acknowledgment_opt_out_and_decision_are_atomic(postgres_dsn: str) -> None:
    fixtures = json.loads((ROOT / "tests/fixtures/closure/ot01_ingress_valid.json").read_text())
    policy = copy.deepcopy(fixtures["AcknowledgmentPolicy"])
    lexicon = copy.deepcopy(fixtures["OptOutLexicon"])
    request = copy.deepcopy(fixtures["AcknowledgmentDecisionRequest"])
    template = b"Hello {{first_name}}"
    policy["rules"][0]["templateDigest"] = f"sha256:{hashlib.sha256(template).hexdigest()}"
    candidate = copy.deepcopy(CATALOG_FIXTURES["Suppression"])
    candidate.update(
        id="ack-suppression-1",
        tenantId="tenant-a",
        subjectId="buyer-person-1",
        endpointId="ack-recipient-1",
        scope="channel_all",
        sourceEvidenceIds=["evidence-1"],
        suppressedAt=request["requestedAt"],
    )
    policy["tenantId"] = lexicon["tenantId"] = request["tenantId"] = "tenant-a"
    request["recipientEndpointId"] = "ack-recipient-1"
    request["subjectId"] = "buyer-person-1"
    identity = _external_message_identity("ack-provider-event-1")
    request["externalMessageIdentityRef"] = {
        "recordId": identity["recordId"],
        "recordType": "ExternalMessageIdentity",
        "version": identity["recordVersion"],
        "status": identity["status"],
    }
    request["suppressionRecordCandidate"] = candidate

    with _runtime_connection(postgres_dsn) as connection:
        canonical = CanonicalRepository(connection, tenant_id="tenant-a")
        canonical.save(
            _dependency(
                "ContactEndpoint",
                "ack-recipient-1",
                endpointType="phone",
                normalizedValue="+15551234567",
                ownerType="person",
                ownerId="buyer-person-1",
            )
        )
        repository = AcknowledgmentRepository(connection, tenant_id="tenant-a")
        PostgresClosureRepository(connection, tenant_id="tenant-a").save(identity)
        repository.admit_config(policy)
        repository.admit_config(lexicon)

        decision = repository.decide(request, " STOP ", template)
        assert decision["disposition"] == "suppress_and_acknowledge"
        assert canonical.get("ack-suppression-1") is not None
        assert repository.decide(request, " STOP ", template) == decision

        conflicting = copy.deepcopy(request)
        conflicting.update(requestId="ack-request-2", idempotencyKey="ack-idem-2")
        conflicting["suppressionRecordCandidate"]["id"] = "ack-suppression-rollback"
        with pytest.raises(psycopg.errors.UniqueViolation):
            repository.decide(conflicting, "stop", template)
        assert canonical.get("ack-suppression-rollback") is None

        outcome = copy.deepcopy(fixtures["AcknowledgmentOutcome"])
        outcome.update(
            tenantId="tenant-a",
            decisionId=request["decisionId"],
            captureEventId=identity["recordId"],
            captureCommittedAt=request["capturedAt"],
        )
        repository.admit_outcome(outcome)
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", ("tenant-a",))
        assert connection.execute(
            "SELECT count(*) FROM ingress_acknowledgment_outcomes WHERE decision_id=%s",
            (request["decisionId"],),
        ).fetchone() == (1,)


def test_habitat_redeems_permit_and_registers_attempt_atomically(postgres_dsn: str) -> None:
    now = datetime(2030, 1, 1, 0, 4, tzinfo=UTC)
    intent = _habitat_intent()
    with _runtime_connection(postgres_dsn) as connection:
        repository = PostgresHabitatRepository(
            connection,
            tenant_id="tenant-a",
            kernel=HabitatKernel(_LockedHabitatStateReader(), _HabitatPolicy()),
            state_reader=_LockedHabitatStateReader(),
            permit_secret=b"test-only-habitat-permit-secret-32",
            token_factory=lambda: b"single-use-token",
        )

        result = repository.admit_and_register(intent, evaluated_at=now)

        assert result.decision.allowed is True
        assert result.permit is not None
        assert result.permit.state == "redeemed"
        assert result.attempt is not None
        assert result.attempt["attemptState"] == "registered"

        replay = repository.admit_and_register(intent, evaluated_at=now)
        assert replay.decision.allowed is False
        assert replay.decision.reason == "permit_replayed"
        assert replay.permit is None
        assert replay.attempt is None

        connection.execute("SELECT set_config('app.tenant_id', %s, true)", ("tenant-a",))
        attempts = connection.execute(
            "SELECT count(*) FROM canonical_records_current "
            "WHERE tenant_id = %s AND record_type = 'EffectAttempt'",
            ("tenant-a",),
        ).fetchone()
        permits = connection.execute(
            "SELECT state, count(*) FROM habitat_effect_permits "
            "WHERE tenant_id = %s GROUP BY state",
            ("tenant-a",),
        ).fetchone()
        assert attempts == (1,)
        assert permits == ("redeemed", 1)


def test_habitat_denial_records_authoritative_versions_without_effect(
    postgres_dsn: str,
) -> None:
    now = datetime(2030, 1, 1, 0, 4, tzinfo=UTC)
    intent = _habitat_intent()
    intent["intent_id"] = "intent-version-denied-1"
    intent["idempotency_key"] = "send:conversation-1:stale"
    intent["target_resource"]["version"] = 3
    intent["canonical_version_vector"]["conversation-1"] = 3
    with _runtime_connection(postgres_dsn) as connection:
        repository = PostgresHabitatRepository(
            connection,
            tenant_id="tenant-a",
            kernel=HabitatKernel(_LockedHabitatStateReader(), _HabitatPolicy()),
            state_reader=_LockedHabitatStateReader(),
            permit_secret=b"test-only-habitat-permit-secret-32",
        )

        result = repository.admit_and_register(intent, evaluated_at=now)

        assert result.decision.reason == "canonical_version_conflict"
        assert result.permit is None
        assert result.attempt is None
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", ("tenant-a",))
        evidence = connection.execute(
            "SELECT decision, reason, authoritative_versions, permit_digest "
            "FROM habitat_authority_decisions WHERE tenant_id = %s AND intent_id = %s",
            ("tenant-a", intent["intent_id"]),
        ).fetchone()
        permits = connection.execute(
            "SELECT count(*) FROM habitat_effect_permits WHERE tenant_id = %s AND intent_id = %s",
            ("tenant-a", intent["intent_id"]),
        ).fetchone()
        attempts = connection.execute(
            "SELECT count(*) FROM canonical_records_current "
            "WHERE tenant_id = %s AND record_type = 'EffectAttempt' "
            "AND record->>'intentId' = %s",
            ("tenant-a", intent["intent_id"]),
        ).fetchone()
        assert evidence == (
            "denied",
            "canonical_version_conflict",
            {"conversation-1": 4, "person-1": 2},
            None,
        )
        assert permits == (0,)
        assert attempts == (0,)


def test_habitat_attempt_and_concurrent_canonical_mutation_have_total_order(
    postgres_dsn: str,
) -> None:
    locked = threading.Event()
    release = threading.Event()
    mutation_started = threading.Event()
    mutation_done = threading.Event()
    intent = _habitat_intent()
    intent.update(
        intent_id="intent-race-admission-first",
        idempotency_key="send:broker-1:race-admission-first",
        target_resource={
            "resource_type": "brokerage",
            "resource_id": "broker-1",
            "version": 1,
        },
        canonical_version_vector={"broker-1": 1, "buyer-person-1": 1},
    )
    blocking_reader = _BlockingHabitatStateReader(locked, release)

    def admit() -> Any:
        with _runtime_connection(postgres_dsn) as connection:
            reader = PostgresVersionLockedStateReader(blocking_reader)
            repository = PostgresHabitatRepository(
                connection,
                tenant_id="tenant-a",
                kernel=HabitatKernel(reader, _HabitatPolicy()),
                state_reader=reader,
                permit_secret=b"test-only-habitat-permit-secret-32",
            )
            return repository.admit_and_register(
                intent, evaluated_at=datetime(2030, 1, 1, 0, 4, tzinfo=UTC)
            )

    def mutate() -> dict[str, Any]:
        with _runtime_connection(postgres_dsn) as connection:
            repository = CanonicalRepository(connection, tenant_id="tenant-a")
            current = repository.get("broker-1")
            assert current is not None
            changed = copy.deepcopy(current)
            changed["version"] = 2
            changed["updatedAt"] = "2030-01-01T00:04:01Z"
            mutation_started.set()
            saved = repository.save(changed, expected_version=1)
            mutation_done.set()
            return saved

    with ThreadPoolExecutor(max_workers=2) as executor:
        admission_future = executor.submit(admit)
        assert locked.wait(timeout=5)
        mutation_future = executor.submit(mutate)
        assert mutation_started.wait(timeout=5)
        assert mutation_done.wait(timeout=0.2) is False
        release.set()
        admission = admission_future.result(timeout=5)
        mutation = mutation_future.result(timeout=5)

    assert admission.decision.allowed is True
    assert admission.attempt is not None
    assert mutation["version"] == 2


def test_habitat_denies_when_canonical_mutation_commits_before_admission(
    postgres_dsn: str,
) -> None:
    with _runtime_connection(postgres_dsn) as connection:
        canonical = CanonicalRepository(connection, tenant_id="tenant-a")
        record = _dependency("Person", "race-person-1")
        canonical.save(record)
        changed = copy.deepcopy(record)
        changed["version"] = 2
        changed["updatedAt"] = "2030-01-01T00:03:59Z"
        canonical.save(changed, expected_version=1)

    intent = _habitat_intent()
    intent.update(
        intent_id="intent-race-mutation-first",
        idempotency_key="send:race-person-1:mutation-first",
        target_resource={
            "resource_type": "person",
            "resource_id": "race-person-1",
            "version": 1,
        },
        canonical_version_vector={"race-person-1": 1},
    )
    with _runtime_connection(postgres_dsn) as connection:
        authority_reader = _LockedHabitatStateReader()
        reader = PostgresVersionLockedStateReader(authority_reader)
        repository = PostgresHabitatRepository(
            connection,
            tenant_id="tenant-a",
            kernel=HabitatKernel(reader, _HabitatPolicy()),
            state_reader=reader,
            permit_secret=b"test-only-habitat-permit-secret-32",
        )

        result = repository.admit_and_register(
            intent, evaluated_at=datetime(2030, 1, 1, 0, 4, tzinfo=UTC)
        )

        assert result.decision.allowed is False
        assert result.decision.reason == "canonical_version_conflict"
        assert result.decision.authoritative_versions == {"race-person-1": 2}
        assert result.permit is None
        assert result.attempt is None


class _FactVerifier:
    def verify(self, rule_id: str, proposition: dict[str, Any], evidence) -> bool:
        return (
            rule_id == "provider-email-v1"
            and proposition["predicate"] == "value"
            and bool(evidence)
        )


def test_real_postgres_reconstructs_versions(postgres_dsn: str) -> None:
    with _runtime_connection(postgres_dsn) as connection:
        repository = CanonicalRepository(connection, tenant_id="tenant-a")
        repository.save(_agreement("tenant-a", record_id="agreement-a", version=1))
        repository.save(
            _agreement("tenant-a", record_id="agreement-a", version=2), expected_version=1
        )

        assert repository.get("agreement-a")["version"] == 2  # type: ignore[index]
        assert [record["version"] for record in repository.history("agreement-a")] == [1, 2]


def test_real_postgres_rejects_orphan_and_wrong_type_references(postgres_dsn: str) -> None:
    with _runtime_connection(postgres_dsn) as connection:
        repository = CanonicalRepository(connection, tenant_id="tenant-a")
        orphan = _agreement("tenant-a", record_id="agreement-orphan", version=1)
        orphan["executedArtifactId"] = "missing-artifact"
        with pytest.raises(ContractViolation, match="REFERENCE_NOT_FOUND"):
            repository.save(orphan)
        wrong_type = _agreement("tenant-a", record_id="agreement-wrong-type", version=1)
        wrong_type["executedArtifactId"] = "broker-1"
        with pytest.raises(ContractViolation, match="REFERENCE_TYPE_MISMATCH"):
            repository.save(wrong_type)


def test_real_postgres_verified_fact_fails_closed_without_rule_verifier(postgres_dsn: str) -> None:
    fact = _dependency(
        "VerifiedFact",
        "verified-fact-1",
        sourceEvidenceIds=["evidence-1"],
        supportingEvidenceIds=["evidence-1"],
        verificationRuleId="provider-email-v1",
    )
    with _runtime_connection(postgres_dsn) as connection:
        with pytest.raises(ContractViolation, match="VERIFICATION_RULE_UNAVAILABLE"):
            CanonicalRepository(connection, tenant_id="tenant-a").save(fact)
        saved = CanonicalRepository(
            connection,
            tenant_id="tenant-a",
            fact_verifier=_FactVerifier(),
        ).save(fact)
        assert saved["recordType"] == "VerifiedFact"


def test_real_postgres_derives_representation_only_from_covering_agreement(
    postgres_dsn: str,
) -> None:
    with _runtime_connection(postgres_dsn) as connection:
        repository = CanonicalRepository(connection, tenant_id="tenant-a")
        with pytest.raises(ContractViolation, match="NON_REPRESENTATION_CANNOT_REPRESENT"):
            repository.save(
                _relationship(agreement_id="agreement-a", record_id="relationship-denied")
            )

        representation = _agreement("tenant-a", record_id="agreement-representation", version=1)
        representation["agreementType"] = "representation"
        representation["serviceDefinitions"] = [
            {"serviceCode": "showing_access", "allowed": True},
            {"serviceCode": "offer_presentation", "allowed": True},
        ]
        repository.save(representation)
        saved = repository.save(
            _relationship(agreement_id="agreement-representation", record_id="relationship-active")
        )
        assert saved["relationshipState"] == "active"
        with pytest.raises(ContractViolation, match="ACTIVE_REPRESENTATION_CARDINALITY"):
            repository.save(
                _relationship(
                    agreement_id="agreement-representation",
                    record_id="relationship-duplicate",
                )
            )


def test_concurrent_active_representation_admission_has_one_winner(
    postgres_dsn: str,
) -> None:
    buying_party = copy.deepcopy(CATALOG_FIXTURES["BuyingParty"])
    buying_party.update(
        {
            "id": "party-representation-race",
            "tenantId": "tenant-a",
            "createdAt": "2029-01-01T00:00:00Z",
            "updatedAt": "2029-01-01T00:00:00Z",
            "effectiveFrom": "2029-01-01T00:00:00Z",
            "createdBy": {"actorType": "service_principal", "actorId": "crm-1"},
            "sourceEvidenceIds": ["evidence-1"],
            "members": [{"personId": "buyer-person-1", "role": "buyer"}],
        }
    )
    agreement = _agreement("tenant-a", record_id="agreement-representation-race", version=1)
    agreement.update(
        {
            "agreementType": "representation",
            "buyerPartyIds": ["party-representation-race"],
            "serviceDefinitions": [{"serviceCode": "showing_access", "allowed": True}],
            "signatureEvidence": [
                {
                    "signerPartyId": "party-representation-race",
                    "signedAt": "2029-01-01T00:00:00Z",
                    "evidenceId": "evidence-1",
                }
            ],
        }
    )
    with _runtime_connection(postgres_dsn) as connection:
        repository = CanonicalRepository(connection, tenant_id="tenant-a")
        repository.save(buying_party)
        repository.save(agreement)

    barrier = threading.Barrier(2)

    def admit(index: int) -> str:
        relationship = _relationship(
            agreement_id="agreement-representation-race",
            record_id=f"relationship-race-{index}",
        )
        relationship["buyingPartyId"] = "party-representation-race"
        with _runtime_connection(postgres_dsn) as connection:
            barrier.wait()
            try:
                CanonicalRepository(connection, tenant_id="tenant-a").save(relationship)
            except ContractViolation as exc:
                assert "ACTIVE_REPRESENTATION_CARDINALITY" in str(exc)
                return "rejected"
            return "admitted"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(admit, range(2)))

    assert sorted(results) == ["admitted", "rejected"]


def test_real_postgres_supersession_closes_and_activates_atomically(postgres_dsn: str) -> None:
    with _runtime_connection(postgres_dsn) as connection:
        repository = CanonicalRepository(connection, tenant_id="tenant-a")
        prior = _agreement("tenant-a", record_id="agreement-a", version=3)
        prior["status"] = "superseded"
        prior["effectiveTo"] = "2029-01-10T00:00:00Z"
        successor = _agreement("tenant-a", record_id="agreement-successor", version=1)
        successor["supersedesId"] = "agreement-a"
        successor["effectiveFrom"] = "2029-01-11T00:00:00Z"
        successor["effectiveAt"] = "2029-01-11T00:00:00Z"
        successor["terminatesAt"] = "2029-01-25T00:00:00Z"

        with pytest.raises(ValueError, match="effectiveTo"):
            repository.supersede(prior, successor, expected_prior_version=2)
        assert repository.get("agreement-a")["version"] == 2  # type: ignore[index]
        assert repository.get("agreement-successor") is None

        successor["effectiveFrom"] = prior["effectiveTo"]
        successor["effectiveAt"] = prior["effectiveTo"]
        successor["terminatesAt"] = "2029-01-24T00:00:00Z"
        closed, activated = repository.supersede(prior, successor, expected_prior_version=2)
        assert closed["status"] == "superseded"
        assert activated["supersedesId"] == "agreement-a"
        assert repository.get("agreement-a")["version"] == 3  # type: ignore[index]
        assert repository.get("agreement-successor")["version"] == 1  # type: ignore[index]


def test_real_postgres_applies_correction_and_replacement_atomically(postgres_dsn: str) -> None:
    with _runtime_connection(postgres_dsn) as connection:
        repository = CanonicalRepository(connection, tenant_id="tenant-a")
        original = _dependency(
            "Assertion",
            "assertion-corrected",
            sourceEvidenceIds=["evidence-1"],
            speakerId="buyer-person-1",
        )
        repository.save(original)
        corrected = copy.deepcopy(original)
        corrected.update(
            version=2,
            updatedAt="2030-01-02T00:00:00Z",
            effectiveTo="2030-01-03T00:00:00Z",
            status="superseded",
            assertionState="superseded",
        )
        replacement = copy.deepcopy(original)
        replacement.update(
            id="assertion-replacement",
            version=1,
            updatedAt="2030-01-03T00:00:00Z",
            effectiveFrom="2030-01-03T00:00:00Z",
            supersedesId="assertion-corrected",
        )
        replacement["proposition"]["value"] = "corrected-value"
        correction = _dependency(
            "Correction",
            "correction-1",
            sourceEvidenceIds=["evidence-1"],
            correctedItemId="assertion-corrected",
            replacementItemId="assertion-replacement",
            correctionAction="replace",
            correctionEvidenceIds=["evidence-1"],
            correctionState="applied",
        )
        repository.apply_correction(
            correction,
            corrected,
            expected_corrected_version=1,
            raw_replacement=replacement,
        )
        assert repository.get("assertion-corrected")["assertionState"] == "superseded"  # type: ignore[index]
        assert repository.get("assertion-replacement")["assertionState"] == "current"  # type: ignore[index]
        assert repository.get("correction-1")["correctionState"] == "applied"  # type: ignore[index]


def test_operator_1_1_applies_evidenced_correction_and_result_atomically(
    postgres_dsn: str,
) -> None:
    with _runtime_connection(postgres_dsn) as connection:
        canonical = CanonicalRepository(connection, tenant_id="tenant-a")
        original = _dependency(
            "Assertion",
            "operator-assertion-1",
            sourceEvidenceIds=["evidence-1"],
            speakerId="buyer-person-1",
        )
        canonical.save(original)
        authorization = _dependency(
            "Authorization",
            "operator-authorization-1",
            sourceEvidenceIds=["evidence-1"],
            grantorType="license_holder",
            grantorId="agent-1",
            granteeType="license_holder",
            granteeId="agent-1",
            actionClass="correct_epistemic_item",
            resourceType="Assertion",
            resourceId="operator-assertion-1",
            grantedAt="2026-01-01T00:00:00Z",
            expiresAt="2030-01-01T00:00:00Z",
            authorizationState="active",
        )
        canonical.save(authorization)
        policy = {
            "message_type": "operator_policy",
            "schema_version": "operator-surface/1.1.0",
            "policy_id": "operator-policy-1",
            "tenant_id": "tenant-a",
            "record_version": 1,
            "effective_from": "2026-01-01T00:00:00Z",
            "status": "active",
            "command_rules": [
                {
                    "command_type": "correct_invalidate",
                    "action_class": "correct_epistemic_item",
                    "target_record_types": ["Assertion"],
                    "actor_types": ["license_holder"],
                },
            ],
            "evidence_refs": [
                {
                    "record_id": "evidence-1",
                    "record_type": "Evidence",
                    "version": 1,
                    "digest": "sha256:" + "a" * 64,
                    "captured_at": "2026-01-01T00:00:00Z",
                }
            ],
        }
        OperatorPolicyRepository(connection, tenant_id="tenant-a").admit(policy)
        ActorTenantAuthorizationRepository(connection, tenant_id="tenant-a").save(
            {
                "schemaVersion": "open-025-027/1.0.0",
                "recordType": "ActorTenantAuthorization",
                "tenantId": "tenant-a",
                "recordId": "operator-actor-grant-1",
                "observedAt": "2026-01-01T00:00:00Z",
                "actorId": "agent-1",
                "principalId": "agent-1",
                "role": "license_holder",
                "allowedCommands": ["correct_invalidate"],
                "recordScopes": ["Assertion"],
                "policyVersion": "operator-policy-1",
                "authorizationVersion": 1,
                "effectiveAt": "2026-01-01T00:00:00Z",
                "expiresAt": "2030-01-01T00:00:00Z",
                "status": "active",
            }
        )

        corrected = copy.deepcopy(original)
        corrected.update(
            version=2,
            updatedAt="2030-01-02T00:00:00Z",
            status="invalid",
            assertionState="invalid",
        )
        correction = _dependency(
            "Correction",
            "operator-correction-1",
            sourceEvidenceIds=["evidence-1"],
            correctedItemId="operator-assertion-1",
            correctionAction="invalidate",
            reason="Buyer supplied evidenced correction.",
            attributedTo={"actorType": "license_holder", "actorId": "agent-1"},
            correctionEvidenceIds=["evidence-1"],
            correctedAt="2030-01-02T00:00:00Z",
            correctionState="applied",
        )
        now = datetime.now(UTC)
        command = {
            "message_type": "operator_command",
            "schema_version": "operator-surface/1.1.0",
            "command_id": "operator-command-correction-1",
            "tenant_id": "tenant-a",
            "journey_id": "journey-operator-1",
            "command_type": "correct_invalidate",
            "target_record_id": "operator-assertion-1",
            "target_record_type": "Assertion",
            "expected_version": 1,
            "authority": {
                "actor_id": "agent-1",
                "actor_type": "license_holder",
                "authorization_refs": [
                    {
                        "record_id": "operator-actor-grant-1",
                        "record_type": "ActorTenantAuthorization",
                        "version": 1,
                        "status": "active",
                    }
                ],
                "policy_ref": {
                    "record_id": "operator-policy-1",
                    "record_type": "OperatorPolicy",
                    "version": 1,
                    "status": "active",
                },
                "action_class": "correct_epistemic_item",
                "resource_type": "Assertion",
                "resource_id": "operator-assertion-1",
                "authenticated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "payload_digest": "",
            "idempotency_key": "operator-correction-key-1",
            "issued_at": (now - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "reason": "Buyer supplied evidenced correction.",
            "mutation": {
                "kind": "correction",
                "correction_record": correction,
                "corrected_item_update": corrected,
            },
        }
        command["payload_digest"] = command_payload_digest(command)
        result = CanonicalOperatorCommandService(
            connection, canonical, tenant_id="tenant-a"
        ).dispatch(command, actor_id="agent-1")

        assert result["status"] == "applied"
        assert result["decision_evidence_id"] == "operator-correction-1"
        assert canonical.get("operator-assertion-1")["assertionState"] == "invalid"  # type: ignore[index]
        assert canonical.get("operator-correction-1")["correctionState"] == "applied"  # type: ignore[index]
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        assert connection.execute(
            "SELECT count(*) FROM operator_command_results WHERE command_id = %s",
            ("operator-command-correction-1",),
        ).fetchone() == (1,)

        rollback_original = copy.deepcopy(original)
        rollback_original["id"] = "operator-assertion-rollback"
        canonical.save(rollback_original)
        rollback_authorization = copy.deepcopy(authorization)
        rollback_authorization.update(
            id="operator-authorization-rollback",
            resourceId="operator-assertion-rollback",
        )
        canonical.save(rollback_authorization)
        rollback_policy = copy.deepcopy(policy)
        rollback_policy["policy_id"] = "operator-policy-rollback"
        OperatorPolicyRepository(connection, tenant_id="tenant-a").admit(rollback_policy)
        rollback_corrected = copy.deepcopy(rollback_original)
        rollback_corrected.update(
            version=2,
            updatedAt="2030-01-02T00:00:00Z",
            status="invalid",
            assertionState="invalid",
        )
        rollback_correction = copy.deepcopy(correction)
        rollback_correction.update(
            id="operator-correction-rollback",
            correctedItemId="operator-assertion-rollback",
        )
        rollback_command = copy.deepcopy(command)
        rollback_command.update(
            command_id="operator-command-result-conflict",
            target_record_id="operator-assertion-rollback",
            idempotency_key="operator-correction-key-rollback",
        )
        rollback_command["authority"]["resource_id"] = "operator-assertion-rollback"
        rollback_command["mutation"] = {
            "kind": "correction",
            "correction_record": rollback_correction,
            "corrected_item_update": rollback_corrected,
        }
        rollback_command["payload_digest"] = command_payload_digest(rollback_command)
        conflict_result = copy.deepcopy(result)
        conflict_result["command_id"] = "operator-command-result-conflict"
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        connection.execute(
            """
            INSERT INTO operator_command_results
                (tenant_id, idempotency_key, payload_digest, command_id, result)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                "tenant-a",
                "preexisting-other-key",
                "sha256:" + "b" * 64,
                "operator-command-result-conflict",
                json.dumps(conflict_result),
            ),
        )
        connection.commit()

        with pytest.raises(psycopg.errors.UniqueViolation):
            CanonicalOperatorCommandService(connection, canonical, tenant_id="tenant-a").dispatch(
                rollback_command, actor_id="agent-1"
            )
        assert canonical.get("operator-assertion-rollback")["version"] == 1  # type: ignore[index]
        assert canonical.get("operator-correction-rollback") is None


def test_concurrent_canonical_updates_allow_exactly_one_version_winner(postgres_dsn: str) -> None:
    with _runtime_connection(postgres_dsn) as connection:
        CanonicalRepository(connection, tenant_id="tenant-a").save(
            _agreement("tenant-a", record_id="agreement-concurrent", version=1)
        )
    barrier = threading.Barrier(2)

    def update(index: int) -> str:
        with _runtime_connection(postgres_dsn) as connection:
            repository = CanonicalRepository(connection, tenant_id="tenant-a")
            candidate = _agreement("tenant-a", record_id="agreement-concurrent", version=2)
            candidate["terminationReason"] = f"concurrency-candidate-{index}"
            barrier.wait()
            try:
                repository.save(candidate, expected_version=1)
            except VersionConflict:
                return "conflict"
            return "saved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(update, range(2)))
    assert sorted(outcomes) == ["conflict", "saved"]
    with _runtime_connection(postgres_dsn) as connection:
        history = CanonicalRepository(connection, tenant_id="tenant-a").history(
            "agreement-concurrent"
        )
        assert [record["version"] for record in history] == [1, 2]


def test_concurrent_identity_resolution_creates_one_mapping_and_explicit_case(
    postgres_dsn: str,
) -> None:
    with _runtime_connection(postgres_dsn) as connection:
        CanonicalRepository(connection, tenant_id="tenant-a").save(_person("person-identity-1"))

    barrier = threading.Barrier(2)

    def resolve(index: int):
        with _runtime_connection(postgres_dsn) as connection:
            repository = IdentityRepository(connection, tenant_id="tenant-a")
            barrier.wait()
            return repository.admit(
                mapping_id=f"mapping-{index}",
                identity_kind="verified_email",
                normalized_identity="buyer@example.com",
                provider_account_ref="email-account-1",
                purpose="buyer_consultation",
                resolution_basis="verified_endpoint",
                outcome="matched",
                evidence_ids=(f"identity-evidence-{index}",),
                effective_from=datetime(2029, 1, 1, tzinfo=UTC),
                person_id="person-identity-1",
                person_version=1,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(resolve, range(2)))
    assert sum(created for _, created in results) == 1
    assert len({mapping.mapping_id for mapping, _ in results}) == 1

    with _runtime_connection(postgres_dsn) as connection:
        repository = IdentityRepository(connection, tenant_id="tenant-a")
        ambiguous, created = repository.admit(
            mapping_id="mapping-ambiguous",
            identity_kind="verified_email",
            normalized_identity="new-endpoint@example.com",
            provider_account_ref="email-account-1",
            purpose="buyer_consultation",
            resolution_basis="explicit_form_identity",
            outcome="ambiguous",
            evidence_ids=("identity-evidence-ambiguous",),
            effective_from=datetime(2029, 1, 2, tzinfo=UTC),
            resolution_case_id="identity-case-1",
            candidate_person_ids=("person-identity-1",),
        )
        assert created
        assert ambiguous.person_id is None
        resolved = repository.resolve_case(
            identity_fingerprint=ambiguous.identity_fingerprint,
            expected_version=1,
            person_id="person-identity-1",
            person_version=1,
            evidence_ids=("authorized-resolution-evidence-1",),
            authority_ref="identity-operator-1",
            effective_from=datetime(2029, 1, 3, tzinfo=UTC),
        )
        assert resolved.outcome == "matched"
        assert resolved.version == 2
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        assert connection.execute(
            "SELECT count(*) FROM external_identity_mapping_versions "
            "WHERE mapping_id = 'mapping-ambiguous'"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT count(*) FROM identity_resolution_cases "
            "WHERE resolution_case_id = 'identity-case-1'"
        ).fetchone() == (1,)
        connection.commit()
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-b', false)")
        assert connection.execute(
            "SELECT count(*) FROM external_identity_mappings_current"
        ).fetchone() == (0,)
        connection.commit()
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        with pytest.raises(psycopg.DatabaseError, match="append-only"):
            connection.execute("DELETE FROM identity_resolution_cases")


def test_real_postgres_rls_hides_other_tenant(postgres_dsn: str) -> None:
    with _runtime_connection(postgres_dsn) as connection:
        tenant_b = CanonicalRepository(connection, tenant_id="tenant-b")
        tenant_b.save(_agreement("tenant-b", record_id="agreement-b", version=1))

        tenant_a = CanonicalRepository(connection, tenant_id="tenant-a")
        assert tenant_a.get("agreement-b") is None
        assert tenant_a.history("agreement-b") == []


def test_real_postgres_history_is_append_only(postgres_dsn: str) -> None:
    with _runtime_connection(postgres_dsn) as connection:
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        with pytest.raises(psycopg.DatabaseError, match="append-only"):
            connection.execute(
                "DELETE FROM canonical_record_versions "
                "WHERE tenant_id = 'tenant-a' AND record_id = 'agreement-a'"
            )


def _derived_contract_fixture(suffix: str) -> tuple[dict[str, Any], dict[str, Any]]:
    qualification = json.loads(
        (ROOT / "tests/fixtures/qualification_readiness/valid.json").read_text()
    )["policy"]
    availability = json.loads(
        (ROOT / "tests/fixtures/availability_booking/valid.json").read_text()
    )["binding"]
    qualification["policyId"] = f"qualification-policy-{suffix}"
    availability["bindingId"] = f"calendar-binding-{suffix}"
    return qualification, availability


def _qualification_decision_pair_fixture(
    suffix: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    fixture = json.loads((ROOT / "tests/fixtures/qualification_readiness/valid.json").read_text())
    policy = fixture["policy"]
    inputs = fixture["input"]
    next_question = fixture["nextQuestion"]
    readiness = fixture["readiness"]
    inputs["inputSetId"] = f"qualification-input-{suffix}"
    next_question["decisionId"] = f"next-question-{suffix}"
    readiness["decisionId"] = f"readiness-{suffix}"
    for decision in (next_question, readiness):
        decision["inputSetRef"]["recordId"] = inputs["inputSetId"]
    return policy, inputs, next_question, readiness


def _booking_outcome_fixture(
    suffix: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    fixture = json.loads((ROOT / "tests/fixtures/availability_booking/valid.json").read_text())
    binding = fixture["binding"]
    command = fixture["command"]
    result = fixture["result"]
    reconciliation = fixture["reconciliation"]
    command["commandId"] = f"booking-command-{suffix}"
    result["resultId"] = f"booking-result-{suffix}"
    result["commandRef"]["recordId"] = command["commandId"]
    reconciliation["reconciliationId"] = f"booking-reconciliation-{suffix}"
    reconciliation["commandRef"]["recordId"] = command["commandId"]
    reconciliation["priorResultRef"]["recordId"] = result["resultId"]
    return binding, command, result, reconciliation


def _slot_set_fixture(
    suffix: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    availability = json.loads((ROOT / "tests/fixtures/availability_booking/valid.json").read_text())
    qualification = json.loads(
        (ROOT / "tests/fixtures/qualification_readiness/valid.json").read_text()
    )
    readiness = qualification["readiness"]
    readiness["expiresAt"] = "2026-03-08T08:05:00Z"
    availability["slotSet"]["slotSetId"] = f"slot-set-{suffix}"
    return (
        availability["policy"],
        readiness,
        availability["binding"],
        availability["snapshot"],
        availability["slotSet"],
    )


def _insert_derived_contract_record(
    connection: psycopg.Connection[Any],
    *,
    family: str,
    record_id: str,
    record_version: int,
    record: dict[str, Any],
) -> None:
    connection.execute(
        "INSERT INTO derived_contract_records "
        "(tenant_id, contract_family, message_type, record_id, record_version, "
        "schema_version, payload) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (
            record["tenantId"],
            family,
            record["messageType"],
            record_id,
            record_version,
            record["schemaVersion"],
            Jsonb(record),
        ),
    )


def _consent_presentation_fixture(*, tenant_id: str, suffix: str) -> dict[str, Any]:
    return {
        "messageType": "consent_presentation_evidence",
        "schemaVersion": "ot01-ingress/1.1.0",
        "evidenceId": f"consent-presentation-{suffix}",
        "tenantId": tenant_id,
        "subjectPersonId": f"person-{suffix}",
        "surface": "web",
        "disclosureArtifactId": f"disclosure-{suffix}",
        "disclosureVersion": "1.0.0",
        "presentedAt": "2030-01-01T00:00:00Z",
        "locale": "en-US",
        "interaction": "presented",
        "payloadDigest": "sha256:" + "a" * 64,
        "retentionClass": "audit_7y",
        "version": 1,
    }


def _attribution_input_fixture(*, tenant_id: str, suffix: str) -> dict[str, Any]:
    return {
        "messageType": "attribution_input",
        "schemaVersion": "ot01-ingress/1.1.0",
        "attributionId": f"attribution-{suffix}",
        "tenantId": tenant_id,
        "sourceType": "web_form",
        "sourceInstanceId": f"source-{suffix}",
        "receivedAt": "2030-01-01T00:00:00Z",
        "payloadDigest": "sha256:" + "a" * 64,
        "provenanceEvidenceId": f"provenance-{suffix}",
        "retentionClass": "operational_90d",
        "version": 1,
    }


def test_new_contract_families_have_durable_storage(postgres_dsn: str) -> None:
    qualification, availability = _derived_contract_fixture("durable")
    with _runtime_connection(postgres_dsn) as connection:
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        _insert_derived_contract_record(
            connection,
            family="qualification_readiness",
            record_id=qualification["policyId"],
            record_version=1,
            record=qualification,
        )
        _insert_derived_contract_record(
            connection,
            family="availability_booking",
            record_id=availability["bindingId"],
            record_version=1,
            record=availability,
        )
        assert connection.execute(
            "SELECT contract_family, message_type, record_id, record_version "
            "FROM derived_contract_records WHERE record_id LIKE '%-durable' "
            "ORDER BY contract_family"
        ).fetchall() == [
            (
                "availability_booking",
                "calendar_provider_binding",
                "calendar-binding-durable",
                1,
            ),
            (
                "qualification_readiness",
                "qualification_policy",
                "qualification-policy-durable",
                1,
            ),
        ]


@pytest.mark.parametrize(
    ("family", "tenant_id", "record_id", "record_version"),
    [
        ("ontology", "tenant-a", "qualification-policy-a", 1),
        ("qualification_readiness", "tenant-b", "qualification-policy-a", 1),
        ("qualification_readiness", "tenant-a", "wrong-policy-id", 1),
        ("qualification_readiness", "tenant-a", "qualification-policy-a", 2),
    ],
)
def test_new_contract_storage_rejects_unadmitted_or_mismatched_envelopes(
    postgres_dsn: str,
    family: str,
    tenant_id: str,
    record_id: str,
    record_version: int,
) -> None:
    qualification = json.loads(
        (ROOT / "tests/fixtures/qualification_readiness/valid.json").read_text()
    )["policy"]
    with _runtime_connection(postgres_dsn) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "INSERT INTO derived_contract_records "
                "(tenant_id, contract_family, message_type, record_id, record_version, "
                "schema_version, payload) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    tenant_id,
                    family,
                    qualification["messageType"],
                    record_id,
                    record_version,
                    qualification["schemaVersion"],
                    Jsonb(qualification),
                ),
            )


def test_new_contract_storage_is_tenant_scoped(postgres_dsn: str) -> None:
    qualification, _ = _derived_contract_fixture("rls")
    with _runtime_connection(postgres_dsn) as connection:
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        _insert_derived_contract_record(
            connection,
            family="qualification_readiness",
            record_id=qualification["policyId"],
            record_version=1,
            record=qualification,
        )
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-b', false)")
        assert connection.execute(
            "SELECT count(*) FROM derived_contract_records "
            "WHERE record_id = 'qualification-policy-rls'"
        ).fetchone() == (0,)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE derived_contract_records SET record_id = 'changed'",
        "DELETE FROM derived_contract_records",
    ],
)
def test_new_contract_storage_is_append_only(postgres_dsn: str, statement: str) -> None:
    qualification, _ = _derived_contract_fixture("append-only")
    with _runtime_connection(postgres_dsn) as connection:
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        _insert_derived_contract_record(
            connection,
            family="qualification_readiness",
            record_id=qualification["policyId"],
            record_version=1,
            record=qualification,
        )
        with pytest.raises(psycopg.DatabaseError, match="append-only"):
            connection.execute(statement + " WHERE record_id = 'qualification-policy-append-only'")


def test_new_contract_storage_rollback_refuses_admitted_record_loss(postgres_dsn: str) -> None:
    qualification, _ = _derived_contract_fixture("rollback")
    with _runtime_connection(postgres_dsn) as connection:
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        _insert_derived_contract_record(
            connection,
            family="qualification_readiness",
            record_id=qualification["policyId"],
            record_version=1,
            record=qualification,
        )
    with psycopg.connect(postgres_dsn, autocommit=True) as admin:
        with pytest.raises(psycopg.DatabaseError, match="rollback refused"):
            admin.execute(DERIVED_CONTRACT_ROLLBACK.read_text())
        assert admin.execute("SELECT to_regclass('derived_contract_records')").fetchone() == (
            "derived_contract_records",
        )


def test_derived_contract_reader_returns_tenant_scoped_structurally_valid_exact_versions(
    postgres_dsn: str,
) -> None:
    qualification, availability = _derived_contract_fixture("reader")
    with _runtime_connection(postgres_dsn) as connection:
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        _insert_derived_contract_record(
            connection,
            family="qualification_readiness",
            record_id=qualification["policyId"],
            record_version=1,
            record=qualification,
        )
        _insert_derived_contract_record(
            connection,
            family="availability_booking",
            record_id=availability["bindingId"],
            record_version=1,
            record=availability,
        )
    reader_a = DerivedContractReader(
        lambda: _runtime_connection(postgres_dsn), tenant_id="tenant-a"
    )
    assert (
        reader_a.get(
            contract_family="qualification_readiness",
            message_type="qualification_policy",
            record_id=qualification["policyId"],
            record_version=1,
        )
        == qualification
    )
    assert (
        reader_a.get(
            contract_family="availability_booking",
            message_type="calendar_provider_binding",
            record_id=availability["bindingId"],
            record_version=1,
        )
        == availability
    )
    assert (
        DerivedContractReader(lambda: _runtime_connection(postgres_dsn), tenant_id="tenant-b").get(
            contract_family="qualification_readiness",
            message_type="qualification_policy",
            record_id=qualification["policyId"],
            record_version=1,
        )
        is None
    )


def test_derived_contract_reader_revalidates_stored_payload(postgres_dsn: str) -> None:
    qualification, _ = _derived_contract_fixture("reader-invalid")
    qualification["criteria"] = []
    with psycopg.connect(postgres_dsn, autocommit=True) as admin:
        admin.execute(
            "INSERT INTO derived_contract_records "
            "(tenant_id, contract_family, message_type, record_id, record_version, "
            "schema_version, payload) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                qualification["tenantId"],
                "qualification_readiness",
                qualification["messageType"],
                qualification["policyId"],
                1,
                qualification["schemaVersion"],
                Jsonb(qualification),
            ),
        )
    with pytest.raises(ContractViolation):
        DerivedContractReader(lambda: _runtime_connection(postgres_dsn), tenant_id="tenant-a").get(
            contract_family="qualification_readiness",
            message_type="qualification_policy",
            record_id=qualification["policyId"],
            record_version=1,
        )


def test_qualification_decision_pair_appends_atomically_and_round_trips(
    postgres_dsn: str,
) -> None:
    policy, inputs, next_question, readiness = _qualification_decision_pair_fixture("atomic")
    repository = QualificationDecisionPairRepository(
        lambda: _runtime_connection(postgres_dsn), tenant_id="tenant-a"
    )
    repository.append_decision_pair(
        policy=policy,
        inputs=inputs,
        next_question=next_question,
        readiness=readiness,
    )

    reader = DerivedContractReader(lambda: _runtime_connection(postgres_dsn), tenant_id="tenant-a")
    assert (
        reader.get(
            contract_family="qualification_readiness",
            message_type="next_question_decision",
            record_id=next_question["decisionId"],
            record_version=1,
        )
        == next_question
    )
    assert (
        reader.get(
            contract_family="qualification_readiness",
            message_type="readiness_decision",
            record_id=readiness["decisionId"],
            record_version=1,
        )
        == readiness
    )


def test_qualification_decision_pair_rolls_back_if_second_insert_conflicts(
    postgres_dsn: str,
) -> None:
    policy, inputs, next_question, readiness = _qualification_decision_pair_fixture("rollback")
    with _runtime_connection(postgres_dsn) as connection:
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        _insert_derived_contract_record(
            connection,
            family="qualification_readiness",
            record_id=readiness["decisionId"],
            record_version=1,
            record=readiness,
        )

    repository = QualificationDecisionPairRepository(
        lambda: _runtime_connection(postgres_dsn), tenant_id="tenant-a"
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        repository.append_decision_pair(
            policy=policy,
            inputs=inputs,
            next_question=next_question,
            readiness=readiness,
        )

    with _runtime_connection(postgres_dsn) as connection:
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        assert connection.execute(
            "SELECT count(*) FROM derived_contract_records "
            "WHERE message_type = 'next_question_decision' AND record_id = %s",
            (next_question["decisionId"],),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM derived_contract_records "
            "WHERE message_type = 'readiness_decision' AND record_id = %s",
            (readiness["decisionId"],),
        ).fetchone() == (1,)


def test_booking_outcomes_append_independently_and_round_trip(postgres_dsn: str) -> None:
    binding, command, result, reconciliation = _booking_outcome_fixture("round-trip")
    repository = BookingOutcomeRepository(
        lambda: _runtime_connection(postgres_dsn), tenant_id="tenant-a"
    )
    repository.append_booking_result(command=command, binding=binding, result=result)
    repository.append_booking_reconciliation(
        command=command,
        binding=binding,
        prior_result=result,
        reconciliation=reconciliation,
    )

    reader = DerivedContractReader(lambda: _runtime_connection(postgres_dsn), tenant_id="tenant-a")
    assert (
        reader.get(
            contract_family="availability_booking",
            message_type="booking_result",
            record_id=result["resultId"],
            record_version=1,
        )
        == result
    )
    assert (
        reader.get(
            contract_family="availability_booking",
            message_type="booking_reconciliation",
            record_id=reconciliation["reconciliationId"],
            record_version=1,
        )
        == reconciliation
    )


@pytest.mark.parametrize("terminal_result", ["confirmed", "cancelled", "failed"])
def test_durable_booking_reconciliation_preserves_terminal_barrier_interpretation(
    postgres_dsn: str,
    terminal_result: str,
) -> None:
    binding, command, result, reconciliation = _booking_outcome_fixture(
        f"barrier-{terminal_result}"
    )
    reconciliation["result"] = terminal_result
    if terminal_result == "failed":
        reconciliation["appointmentRef"] = None
        reconciliation["appointmentVersion"] = None

    repository = BookingOutcomeRepository(
        lambda: _runtime_connection(postgres_dsn), tenant_id="tenant-a"
    )
    repository.append_booking_result(command=command, binding=binding, result=result)
    repository.append_booking_reconciliation(
        command=command,
        binding=binding,
        prior_result=result,
        reconciliation=reconciliation,
    )

    reader = DerivedContractReader(lambda: _runtime_connection(postgres_dsn), tenant_id="tenant-a")
    stored_result = reader.get(
        contract_family="availability_booking",
        message_type="booking_result",
        record_id=result["resultId"],
        record_version=1,
    )
    stored_reconciliation = reader.get(
        contract_family="availability_booking",
        message_type="booking_reconciliation",
        record_id=reconciliation["reconciliationId"],
        record_version=1,
    )

    assert stored_result is not None
    assert stored_reconciliation is not None
    assert stored_result == result
    assert stored_reconciliation == reconciliation
    assert (
        require_unknown_outcome_resolution(stored_result, stored_reconciliation) == terminal_result
    )


@pytest.mark.parametrize("nonterminal_result", ["still_unknown", "conflict_requires_resolution"])
def test_durable_booking_reconciliation_preserves_nonterminal_barrier_interpretation(
    postgres_dsn: str,
    nonterminal_result: str,
) -> None:
    binding, command, result, reconciliation = _booking_outcome_fixture(
        f"barrier-{nonterminal_result}"
    )
    reconciliation["result"] = nonterminal_result
    reconciliation["appointmentRef"] = None
    reconciliation["appointmentVersion"] = None

    repository = BookingOutcomeRepository(
        lambda: _runtime_connection(postgres_dsn), tenant_id="tenant-a"
    )
    repository.append_booking_result(command=command, binding=binding, result=result)
    repository.append_booking_reconciliation(
        command=command,
        binding=binding,
        prior_result=result,
        reconciliation=reconciliation,
    )

    reader = DerivedContractReader(lambda: _runtime_connection(postgres_dsn), tenant_id="tenant-a")
    stored_result = reader.get(
        contract_family="availability_booking",
        message_type="booking_result",
        record_id=result["resultId"],
        record_version=1,
    )
    stored_reconciliation = reader.get(
        contract_family="availability_booking",
        message_type="booking_reconciliation",
        record_id=reconciliation["reconciliationId"],
        record_version=1,
    )

    assert stored_result is not None
    assert stored_reconciliation is not None
    assert stored_result == result
    assert stored_reconciliation == reconciliation
    with pytest.raises(ContractSemanticError, match="reconciliation_required"):
        require_unknown_outcome_resolution(stored_result, stored_reconciliation)


def test_durable_unknown_outcome_without_reconciliation_remains_blocked_and_tenant_scoped(
    postgres_dsn: str,
) -> None:
    binding, command, result, _ = _booking_outcome_fixture("barrier-absent")
    BookingOutcomeRepository(
        lambda: _runtime_connection(postgres_dsn), tenant_id="tenant-a"
    ).append_booking_result(command=command, binding=binding, result=result)

    reader = DerivedContractReader(lambda: _runtime_connection(postgres_dsn), tenant_id="tenant-a")
    stored_result = reader.get(
        contract_family="availability_booking",
        message_type="booking_result",
        record_id=result["resultId"],
        record_version=1,
    )
    assert stored_result is not None
    assert stored_result == result
    with pytest.raises(ContractSemanticError, match="reconciliation_required"):
        require_unknown_outcome_resolution(stored_result, None)
    assert (
        DerivedContractReader(lambda: _runtime_connection(postgres_dsn), tenant_id="tenant-b").get(
            contract_family="availability_booking",
            message_type="booking_result",
            record_id=result["resultId"],
            record_version=1,
        )
        is None
    )


@pytest.mark.parametrize("target", ["result", "reconciliation"])
def test_booking_outcome_duplicate_is_raw_append_conflict_without_mutation(
    postgres_dsn: str,
    target: str,
) -> None:
    binding, command, result, reconciliation = _booking_outcome_fixture(f"duplicate-{target}")
    repository = BookingOutcomeRepository(
        lambda: _runtime_connection(postgres_dsn), tenant_id="tenant-a"
    )
    if target == "result":
        append = lambda: repository.append_booking_result(  # noqa: E731
            command=command, binding=binding, result=result
        )
        record_id = result["resultId"]
    else:
        append = lambda: repository.append_booking_reconciliation(  # noqa: E731
            command=command,
            binding=binding,
            prior_result=result,
            reconciliation=reconciliation,
        )
        record_id = reconciliation["reconciliationId"]
    append()
    with pytest.raises(psycopg.errors.UniqueViolation):
        append()

    with _runtime_connection(postgres_dsn) as connection:
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        assert connection.execute(
            "SELECT count(*) FROM derived_contract_records WHERE record_id = %s",
            (record_id,),
        ).fetchone() == (1,)


def test_slot_set_repository_round_trips_and_duplicate_remains_append_conflict(
    postgres_dsn: str,
) -> None:
    policy, readiness, binding, snapshot, slot_set = _slot_set_fixture("round-trip")
    repository = SlotSetRepository(lambda: _runtime_connection(postgres_dsn), tenant_id="tenant-a")

    repository.append_slot_set(
        policy=policy,
        readiness=readiness,
        binding=binding,
        snapshot=snapshot,
        slot_set=slot_set,
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        repository.append_slot_set(
            policy=policy,
            readiness=readiness,
            binding=binding,
            snapshot=snapshot,
            slot_set=slot_set,
        )

    reader = DerivedContractReader(lambda: _runtime_connection(postgres_dsn), tenant_id="tenant-a")
    assert (
        reader.get(
            contract_family="availability_booking",
            message_type="slot_set",
            record_id=slot_set["slotSetId"],
            record_version=1,
        )
        == slot_set
    )
    assert (
        DerivedContractReader(lambda: _runtime_connection(postgres_dsn), tenant_id="tenant-b").get(
            contract_family="availability_booking",
            message_type="slot_set",
            record_id=slot_set["slotSetId"],
            record_version=1,
        )
        is None
    )


def test_database_rejects_record_envelope_mismatch(postgres_dsn: str) -> None:
    with _runtime_connection(postgres_dsn) as connection:
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                INSERT INTO canonical_records_current
                    (tenant_id, record_id, version, record_type, schema_version, record)
                VALUES
                    ('tenant-a', 'corrupt-1', 1, 'Person', 'buyer-ops/0.3.0', '{}'::jsonb)
                """
            )


def test_rollback_refuses_to_discard_canonical_data(postgres_dsn: str) -> None:
    with psycopg.connect(postgres_dsn, autocommit=True) as admin:
        with pytest.raises(psycopg.DatabaseError, match="rollback is prohibited"):
            admin.execute(ROLLBACK.read_text())
        assert admin.execute("SELECT to_regclass('canonical_records_current')").fetchone() == (
            "canonical_records_current",
        )


def test_evidence_ledger_is_tenant_scoped_and_append_only(postgres_dsn: str) -> None:
    with _runtime_connection(postgres_dsn) as connection:
        repository = EvidenceRepository(connection, tenant_id="tenant-a")
        first = repository.append(
            event_id="evidence-event-1",
            event_type="canonical_mutation",
            occurred_at="2029-01-01T00:00:00Z",
            captured_at="2029-01-01T00:00:01Z",
            classification="confidential",
            retention_class="canonical_mutation",
            purpose="audit_reconstruction",
            payload_digest="sha256:" + ("a" * 64),
            provenance_refs=("agreement-a@2",),
            canonical_record_ids=("agreement-a",),
        )
        second = repository.append(
            event_id="evidence-event-2",
            event_type="provider_receipt",
            occurred_at="2029-01-01T00:01:00-06:00",
            captured_at="2029-01-01T00:01:01-06:00",
            classification="restricted",
            retention_class="provider_receipt",
            purpose="effect_reconciliation",
            payload_digest="sha256:" + ("b" * 64),
            provenance_refs=("provider-receipt-1",),
            effect_attempt_id="effect-1",
        )
        assert second.sequence == 2
        assert second.prior_hash == first.entry_hash
        assert repository.reconstruct() == [first, second]
        assert repository.retrieve(
            allowed_purposes=frozenset({"effect_reconciliation"}),
            allowed_classifications=frozenset({"restricted"}),
        ) == [second]

        key = Ed25519PrivateKey.generate()
        checkpoint = repository.create_checkpoint(
            through_sequence=2,
            signer_key_id="evidence-key-1",
            private_key=key,
        )
        verify_checkpoint(checkpoint, key.public_key())
        audit_export = repository.export_tenant_evidence(
            generated_at=datetime(2029, 1, 1, 1, tzinfo=UTC)
        )
        verify_tenant_export(
            audit_export,
            checkpoint_keys={"evidence-key-1": key.public_key()},
        )
        assert "source artifact containing private data" not in audit_export.to_json()
        with pytest.raises(EvidenceIntegrityError, match="export digest mismatch"):
            verify_tenant_export(
                replace(audit_export, generated_at="changed"),
                checkpoint_keys={"evidence-key-1": key.public_key()},
            )

        connection.execute("SELECT set_config('app.tenant_id', 'tenant-b', false)")
        assert connection.execute("SELECT count(*) FROM evidence_ledger").fetchone() == (0,)
        connection.commit()

        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        with pytest.raises(psycopg.DatabaseError, match="append-only"):
            connection.execute("UPDATE evidence_ledger SET purpose = 'changed' WHERE sequence = 1")


def test_concurrent_evidence_appends_have_one_unbroken_order(postgres_dsn: str) -> None:
    barrier = threading.Barrier(2)

    def append(index: int) -> int:
        with _runtime_connection(postgres_dsn) as connection:
            repository = EvidenceRepository(connection, tenant_id="tenant-a")
            barrier.wait()
            return repository.append(
                event_id=f"concurrent-event-{index}",
                event_type="workflow_transition",
                occurred_at=f"2029-01-01T00:0{index + 2}:00Z",
                captured_at=f"2029-01-01T00:0{index + 2}:01Z",
                classification="confidential",
                retention_class="workflow_transition",
                purpose="audit_reconstruction",
                payload_digest="sha256:" + (str(index + 1) * 64),
                provenance_refs=(f"workflow-run-{index}",),
                workflow_id=f"workflow-{index}",
            ).sequence

    with ThreadPoolExecutor(max_workers=2) as executor:
        sequences = list(executor.map(append, range(2)))

    assert sorted(sequences) == [3, 4]
    with _runtime_connection(postgres_dsn) as connection:
        reconstructed = EvidenceRepository(connection, tenant_id="tenant-a").reconstruct()
        assert [entry.sequence for entry in reconstructed] == [1, 2, 3, 4]


def test_deletion_writes_fence_tombstone_and_all_invalidation_requests(
    postgres_dsn: str,
) -> None:
    policy = RetentionPolicy(
        RetentionConfiguration(
            policy_version="retention/1",
            owner_ref="broker-policy-owner-1",
            effective_at=datetime(2029, 1, 1, tzinfo=UTC),
            deletion_completion_slo=timedelta(hours=24),
            period_years_by_record_class={"buyer_communication": 1},
            object_lock_record_classes=frozenset(),
        ),
        required_record_classes=frozenset({"buyer_communication"}),
    )
    with _runtime_connection(postgres_dsn) as connection:
        repository = EvidenceRepository(connection, tenant_id="tenant-a")
        artifacts = ArtifactRepository(connection, tenant_id="tenant-a")
        artifact = artifacts.register(
            ArtifactPointer(
                artifact_id="artifact-private-1",
                encrypted_object_ref="opaque://artifact-private-1",
                encryption_key_ref="kms://tenant-a/evidence-key/1",
                artifact_digest="sha256:" + ("e" * 64),
            ),
            provenance={"source": "inbound_communication"},
            classification="confidential",
            retention_class="buyer_communication",
            purpose="audit_reconstruction",
            captured_at=datetime(2029, 1, 1, tzinfo=UTC),
            retain_until=datetime(2030, 1, 1, tzinfo=UTC),
        )
        assert (
            artifacts.retrieve(
                "artifact-private-1",
                allowed_purposes=frozenset({"unrelated_purpose"}),
                allowed_classifications=frozenset({"confidential"}),
            )
            is None
        )
        assert (
            artifacts.retrieve(
                "artifact-private-1",
                allowed_purposes=frozenset({"audit_reconstruction"}),
                allowed_classifications=frozenset({"confidential"}),
            )
            == artifact
        )
        artifacts.record_legal_hold(
            hold_event_id="hold-event-1",
            hold_id="hold-1",
            artifact_id="artifact-private-1",
            action="placed",
            authority_ref="broker-policy-owner-1",
            occurred_at=datetime(2030, 1, 1, tzinfo=UTC),
            evidence_event_id="evidence-event-1",
        )
        assert artifacts.active_legal_holds("artifact-private-1") == frozenset({"hold-1"})
        with pytest.raises(PermissionError, match="legal hold"):
            artifacts.mark_deleted(
                "artifact-private-1",
                expected_version=1,
                tombstone_id="tombstone-1",
                state="deleted",
                now=datetime(2031, 1, 2, tzinfo=UTC),
            )
        artifacts.record_legal_hold(
            hold_event_id="hold-event-2",
            hold_id="hold-1",
            artifact_id="artifact-private-1",
            action="released",
            authority_ref="broker-policy-owner-1",
            occurred_at=datetime(2031, 1, 1, tzinfo=UTC),
            evidence_event_id="evidence-event-2",
        )
        assert artifacts.active_legal_holds("artifact-private-1") == frozenset()
        with pytest.raises(DeletionDenied):
            repository.request_deletion(
                event_id="deletion-denied",
                tombstone_id="tombstone-denied",
                fence_id="fence-denied",
                target_ref="artifact-private-1",
                target_kind="evidence",
                deleted_record_class="buyer_communication",
                reason_code="retention_expired",
                occurred_at="2031-01-02T00:00:00Z",
                captured_at="2031-01-02T00:00:01Z",
                classification="confidential",
                retention_class="deletion_event",
                purpose="verified_deletion",
                payload_digest="sha256:" + ("d" * 64),
                provenance_refs=("deletion-request-1",),
                enabled_derived_stores=frozenset({"pgvector", "neo4j"}),
                retention_policy=policy,
                retain_until=datetime(2030, 1, 1, tzinfo=UTC),
                active_legal_hold_ids=frozenset({"hold-1"}),
                now=datetime(2031, 1, 2, tzinfo=UTC),
            )

        receipt = repository.request_deletion(
            event_id="deletion-accepted",
            tombstone_id="tombstone-1",
            fence_id="fence-1",
            target_ref="artifact-private-1",
            target_kind="evidence",
            deleted_record_class="buyer_communication",
            reason_code="retention_expired",
            occurred_at="2031-01-02T00:00:00Z",
            captured_at="2031-01-02T00:00:01Z",
            classification="confidential",
            retention_class="deletion_event",
            purpose="verified_deletion",
            payload_digest="sha256:" + ("d" * 64),
            provenance_refs=("deletion-request-1",),
            enabled_derived_stores=frozenset({"pgvector", "neo4j"}),
            retention_policy=policy,
            retain_until=datetime(2030, 1, 1, tzinfo=UTC),
            active_legal_hold_ids=frozenset(),
            now=datetime(2031, 1, 2, tzinfo=UTC),
        )
        assert receipt.evidence_entry.sequence == 5
        assert receipt.fence_sequence == 1
        assert len(receipt.invalidation_event_ids) == 2
        assert artifacts.is_fenced("artifact-private-1")
        deleted = artifacts.mark_deleted(
            "artifact-private-1",
            expected_version=1,
            tombstone_id="tombstone-1",
            state="deleted",
            now=datetime(2031, 1, 2, tzinfo=UTC),
        )
        assert deleted.version == 2
        assert (
            artifacts.retrieve(
                "artifact-private-1",
                allowed_purposes=frozenset({"audit_reconstruction"}),
                allowed_classifications=frozenset({"confidential"}),
            )
            is None
        )

        propagation = DeletionPropagationRepository(connection, tenant_id="tenant-a")
        initial = propagation.status(
            "tombstone-1", completion_slo=timedelta(hours=24), now=datetime(2031, 1, 2, tzinfo=UTC)
        )
        assert not initial.complete
        propagation.acknowledge(
            invalidation_event_id="pgvector-deleted-1",
            tombstone_id="tombstone-1",
            derived_store="pgvector",
            action="deleted",
            occurred_at=datetime(2031, 1, 2, 1, tzinfo=UTC),
            worker_ref="projection-worker-1",
        )
        propagation.acknowledge(
            invalidation_event_id="neo4j-failed-1",
            tombstone_id="tombstone-1",
            derived_store="neo4j",
            action="failed",
            occurred_at=datetime(2031, 1, 2, 1, tzinfo=UTC),
            worker_ref="projection-worker-2",
        )
        failed = propagation.status(
            "tombstone-1",
            completion_slo=timedelta(hours=24),
            now=datetime(2031, 1, 3, 0, 0, 1, tzinfo=UTC),
        )
        assert not failed.complete
        assert failed.overdue
        assert failed.failed_stores == frozenset({"neo4j"})
        propagation.acknowledge(
            invalidation_event_id="neo4j-unsupported-2",
            tombstone_id="tombstone-1",
            derived_store="neo4j",
            action="unsupported",
            occurred_at=datetime(2031, 1, 3, 1, tzinfo=UTC),
            worker_ref="projection-worker-2",
        )
        completed = propagation.status(
            "tombstone-1",
            completion_slo=timedelta(hours=24),
            now=datetime(2031, 1, 3, 1, tzinfo=UTC),
        )
        assert completed.complete
        assert not completed.overdue

        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        tombstone = connection.execute(
            "SELECT deleted_record_class, reason_code FROM evidence_deletion_tombstones"
        ).fetchone()
        assert tombstone == ("buyer_communication", "retention_expired")
        assert connection.execute(
            "SELECT count(*) FROM derived_invalidation_events WHERE action = 'requested'"
        ).fetchone() == (2,)


def test_telemetry_series_limit_is_atomic_in_postgres(postgres_dsn: str) -> None:
    catalog = copy.deepcopy(load_metric_catalog())
    catalog["dimensionPolicy"]["maximumSeriesPerMetric"] = 1

    def observation(observation_id: str, channel: str) -> dict[str, Any]:
        return {
            "messageType": "metric_observation",
            "schemaVersion": "telemetry-slo/1.0.0",
            "observationId": observation_id,
            "metricId": "capture_latency_seconds",
            "value": 12,
            "unit": "seconds",
            "eventStartedAt": "2030-01-01T00:00:00Z",
            "eventEndedAt": "2030-01-01T00:00:12Z",
            "observedAt": "2030-01-01T00:00:13Z",
            "dimensions": {"environment": "production", "channel": channel},
            "sourceEventIds": [f"ingress-{observation_id}", f"capture-{observation_id}"],
            "producerId": "ingress-service",
            "retentionClass": "operational_90d",
        }

    with _runtime_connection(postgres_dsn) as connection:
        recorder = TelemetryRecorder(connection, tenant_id="tenant-a", catalog=catalog)
        recorder.record_observation(observation("series-web-1", "web"))
        recorder.record_observation(observation("series-web-2", "web"))
        with pytest.raises(ContractViolation, match="SERIES_LIMIT"):
            recorder.record_observation(observation("series-sms-1", "sms"))
        connection.execute("SELECT set_config('app.tenant_id', 'tenant-a', false)")
        assert connection.execute(
            "SELECT count(*) FROM telemetry_observations "
            "WHERE metric_id = 'capture_latency_seconds'"
        ).fetchone() == (2,)


def test_evidence_rollback_refuses_to_discard_ledger(postgres_dsn: str) -> None:
    with psycopg.connect(postgres_dsn, autocommit=True) as admin:
        with pytest.raises(psycopg.DatabaseError, match="rollback is prohibited"):
            admin.execute(EVIDENCE_ROLLBACK.read_text())
        assert admin.execute("SELECT to_regclass('evidence_ledger')").fetchone() == (
            "evidence_ledger",
        )


def test_activation_concurrency_rollback_refuses_decision_loss(postgres_dsn: str) -> None:
    with psycopg.connect(postgres_dsn, autocommit=True) as admin:
        with pytest.raises(psycopg.DatabaseError, match="rollback refused"):
            admin.execute(RELEASE_ACTIVATION_CONCURRENCY_ROLLBACK.read_text())
        assert admin.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'release_activation_decisions' "
            "AND column_name = 'activation_version'"
        ).fetchone() == ("activation_version",)
