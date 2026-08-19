from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime

import pytest

from buyer_ops_contracts.release_activation_v1 import (
    Open025ReleaseSignerAuthority,
    ReleaseActivationRepository,
    SelectedReleaseActivationAuthority,
)


def _activation(**overrides: object) -> dict:
    record = {
        "schemaVersion": "open-025-027/1.0.0",
        "recordType": "ReleaseActivation",
        "tenantId": "tenant-1",
        "recordId": "activation-1",
        "observedAt": "2026-08-19T12:00:00Z",
        "environment": "staging",
        "releaseId": "release-1",
        "buildDigest": "sha256:" + "a" * 64,
        "contractManifestDigest": "sha256:" + "b" * 64,
        "policyVersion": "policy-1",
        "enabledCapabilities": ["connector-1:send"],
        "requiredGateIds": ["GATE-001"],
        "gateEvidence": [
            {
                "gateId": "GATE-001",
                "applicability": "platform_invariant",
                "outcome": "pass",
                "evidenceId": "evidence-1",
                "evidenceDigest": "sha256:" + "c" * 64,
                "expiresAt": "2026-08-20T12:00:00Z",
            }
        ],
        "signerActorId": "release-manager",
        "signature": "sha256:" + "0" * 64,
        "effectiveAt": "2026-08-19T11:00:00Z",
        "expiresAt": "2026-08-20T12:00:00Z",
        "status": "active",
    }
    record.update(overrides)
    unsigned = dict(record)
    unsigned["signature"] = "sha256:" + "0" * 64
    record["signature"] = "sha256:" + hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return record


class _SignatureVerifier:
    def verify(self, activation: dict) -> bool:
        unsigned = dict(activation)
        unsigned["signature"] = "sha256:" + "0" * 64
        expected = "sha256:" + hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return activation["signature"] == expected


class _SignerAuthority:
    def __init__(self, authorized: bool = True) -> None:
        self.authorized = authorized

    def verify(self, activation: dict, *, evaluated_at: datetime) -> bool:
        return self.authorized and activation["signerActorId"] == "release-manager"


class _Cursor:
    def __init__(self) -> None:
        self.payload: dict | None = None
        self.rows: list[tuple[dict]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        if "INSERT INTO release_activation_versions" in statement:
            self.payload = dict(parameters[5].obj)

    def fetchone(self) -> tuple[dict] | None:
        return None if self.payload is None else (self.payload,)

    def fetchall(self) -> list[tuple[dict]]:
        return self.rows


class _Connection:
    def __init__(self) -> None:
        self.cursor_instance = _Cursor()

    def cursor(self) -> _Cursor:
        return self.cursor_instance

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


def _repository(
    connection: _Connection, *, authorized: bool = True
) -> ReleaseActivationRepository:
    return ReleaseActivationRepository(
        connection,  # type: ignore[arg-type]
        tenant_id="tenant-1",
        signature_verifier=_SignatureVerifier(),
        signer_authority=_SignerAuthority(authorized),
    )


def test_readback_returns_exact_signed_payload_and_verification_result() -> None:
    connection = _Connection()
    repository = _repository(connection)
    record = _activation()
    admitted = repository.admit(record, evaluated_at=datetime(2026, 8, 19, 12, tzinfo=UTC))
    payload, verified = repository.readback(
        "activation-1", evaluated_at=datetime(2026, 8, 19, 12, tzinfo=UTC)
    )
    assert admitted == record
    assert payload == record
    assert verified is True


def test_digest_mutation_signer_denial_expiry_and_capability_omission_fail_closed() -> None:
    connection = _Connection()
    repository = _repository(connection)
    repository.admit(_activation(), evaluated_at=datetime(2026, 8, 19, 12, tzinfo=UTC))
    connection.cursor_instance.payload = copy.deepcopy(connection.cursor_instance.payload)
    connection.cursor_instance.payload["buildDigest"] = "sha256:" + "f" * 64
    assert repository.readback(
        "activation-1", evaluated_at=datetime(2026, 8, 19, 12, tzinfo=UTC)
    )[1] is False

    connection.cursor_instance.payload = _activation()
    assert _repository(connection, authorized=False).readback(
        "activation-1", evaluated_at=datetime(2026, 8, 19, 12, tzinfo=UTC)
    )[1] is False
    assert repository.capability_activated(
        "activation-1", "connector-2:send", evaluated_at=datetime(2026, 8, 19, 12, tzinfo=UTC)
    ) is False
    assert repository.capability_activated(
        "activation-1", "connector-1:send", evaluated_at=datetime(2026, 8, 21, tzinfo=UTC)
    ) is False


def test_admission_rejects_unauthorized_signer_and_voice() -> None:
    with pytest.raises(ValueError, match="signer is not currently authorized"):
        _repository(_Connection(), authorized=False).admit(
            _activation(), evaluated_at=datetime(2026, 8, 19, 12, tzinfo=UTC)
        )
    with pytest.raises(Exception, match="PROHIBITED_CAPABILITY"):
        _repository(_Connection()).admit(
            _activation(enabledCapabilities=["outbound_ai_voice"]),
            evaluated_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
        )


def test_signer_authority_and_connector_mapping_have_no_implementation_defaults() -> None:
    connection = _Connection()
    connection.cursor_instance.rows = [
        (
            {
                "schemaVersion": "open-025-027/1.0.0",
                "recordType": "ActorTenantAuthorization",
                "tenantId": "tenant-1",
                "recordId": "grant-1",
                "observedAt": "2026-08-19T11:00:00Z",
                "actorId": "release-manager",
                "principalId": "principal-1",
                "role": "release_manager",
                "allowedCommands": ["owner_declared_activation_command"],
                "recordScopes": ["release-1"],
                "policyVersion": "policy-1",
                "authorizationVersion": 1,
                "effectiveAt": "2026-08-19T11:00:00Z",
                "expiresAt": "2026-08-20T12:00:00Z",
                "status": "active",
            },
        )
    ]
    authority = Open025ReleaseSignerAuthority(
        connection,  # type: ignore[arg-type]
        tenant_id="tenant-1",
        authority_command="owner_declared_activation_command",
        scope=lambda activation: str(activation["releaseId"]),
    )
    assert authority.verify(
        _activation(), evaluated_at=datetime(2026, 8, 19, 12, tzinfo=UTC)
    )
    denied = _activation(policyVersion="policy-2")
    assert not authority.verify(denied, evaluated_at=datetime(2026, 8, 19, 12, tzinfo=UTC))

    connection.cursor_instance.payload = _activation()
    selected = SelectedReleaseActivationAuthority(
        _repository(connection),
        activation_record_id=lambda request: str(request["activationRecordId"]),
        capability_id=lambda request: f"{request['connectorId']}:{request['capability']}",
    )
    assert selected.authorizes(
        {
            "activationRecordId": "activation-1",
            "connectorId": "connector-1",
            "capability": "send",
        },
        evaluated_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
    )
