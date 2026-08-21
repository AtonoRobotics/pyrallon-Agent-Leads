"""OPEN-026 exact-payload activation persistence and fail-closed readback."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, cast

from psycopg.types.json import Jsonb

from .actor_authorization import ActorTenantAuthorizationRepository
from .authority_activation_fair_housing import validate_authority_activation_fair_housing_semantics
from .canonical_repository import Connection
from .structural import validate_record


class ReleaseActivationSignatureVerifier(Protocol):
    """Verify the contract's opaque signature using owner-configured semantics."""

    def verify(self, activation: dict[str, Any]) -> bool: ...


class ReleaseActivationSignerAuthority(Protocol):
    """Verify signer activation authority under the current OPEN-025 grant."""

    def verify(self, activation: dict[str, Any], *, evaluated_at: datetime) -> bool: ...


class Open025ReleaseSignerAuthority:
    """Resolve declared activation authority from the current OPEN-025 grant."""

    def __init__(
        self,
        connection: Connection,
        *,
        tenant_id: str,
        authority_command: str,
    ) -> None:
        if not authority_command:
            raise ValueError("authority_command must be supplied by governing configuration")
        self._repository = ActorTenantAuthorizationRepository(connection, tenant_id=tenant_id)
        self._tenant_id = tenant_id
        self._authority_command = authority_command

    def verify(self, activation: dict[str, Any], *, evaluated_at: datetime) -> bool:
        if activation.get("tenantId") != self._tenant_id:
            return False
        grant = self._repository.current(str(activation.get("signerActorId", "")), now=evaluated_at)
        return bool(
            grant
            and self._authority_command in grant["allowedCommands"]
            and activation.get("releaseId") in grant["recordScopes"]
            and grant["policyVersion"] == activation["policyVersion"]
        )


class ReleaseActivationRepository:
    def __init__(
        self,
        connection: Connection,
        *,
        tenant_id: str,
        signature_verifier: ReleaseActivationSignatureVerifier,
        signer_authority: ReleaseActivationSignerAuthority,
    ) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._connection = connection
        self._tenant_id = tenant_id
        self._signature_verifier = signature_verifier
        self._signer_authority = signer_authority

    def admit(
        self, activation: dict[str, Any], *, evaluated_at: datetime | None = None
    ) -> dict[str, Any]:
        now = (evaluated_at or datetime.now(UTC)).astimezone(UTC)
        validate_record(activation, "authority_activation_fair_housing")
        if activation.get("recordType") != "ReleaseActivation":
            raise ValueError("only ReleaseActivation can be admitted here")
        if activation["tenantId"] != self._tenant_id:
            raise ValueError("activation tenant mismatch")
        validate_authority_activation_fair_housing_semantics(activation, now=now)
        if not self._signature_verifier.verify(activation):
            raise ValueError("release activation signature is invalid")
        if not self._signer_authority.verify(activation, evaluated_at=now):
            raise ValueError("release activation signer is not currently authorized")
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    INSERT INTO release_activation_versions (
                        tenant_id, record_id, environment, release_id, status,
                        payload, observed_at, effective_at, expires_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """.strip(),
                    (
                        self._tenant_id,
                        activation["recordId"],
                        activation["environment"],
                        activation["releaseId"],
                        activation["status"],
                        Jsonb(activation),
                        activation["observedAt"],
                        activation["effectiveAt"],
                        activation["expiresAt"],
                    ),
                )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return activation

    def readback(
        self, record_id: str, *, evaluated_at: datetime | None = None
    ) -> tuple[dict[str, Any], bool]:
        activation = self._load(record_id)
        if activation is None:
            raise KeyError(record_id)
        now = (evaluated_at or datetime.now(UTC)).astimezone(UTC)
        verified = self._verify_current(activation, evaluated_at=now)
        return activation, verified

    def capability_activated(
        self, record_id: str, capability_id: str, *, evaluated_at: datetime | None = None
    ) -> bool:
        try:
            activation, verified = self.readback(record_id, evaluated_at=evaluated_at)
        except KeyError:
            return False
        return verified and capability_id in activation["enabledCapabilities"]

    def _verify_current(self, activation: dict[str, Any], *, evaluated_at: datetime) -> bool:
        try:
            validate_record(activation, "authority_activation_fair_housing")
            validate_authority_activation_fair_housing_semantics(activation, now=evaluated_at)
        except Exception:
            # ContractViolation and structural errors are verification failures. Do not
            # let malformed persisted state turn into an activation.
            return False
        return (
            activation.get("recordType") == "ReleaseActivation"
            and activation.get("tenantId") == self._tenant_id
            and activation.get("status") == "active"
            and self._signature_verifier.verify(activation)
            and self._signer_authority.verify(activation, evaluated_at=evaluated_at)
        )

    def _load(self, record_id: str) -> dict[str, Any] | None:
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    """
                    SELECT payload FROM release_activation_versions
                    WHERE tenant_id = %s AND record_id = %s
                    """.strip(),
                    (self._tenant_id, record_id),
                )
                row = cursor.fetchone()
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return None if row is None else cast(dict[str, Any], row[0])

    def _set_tenant(self, cursor: Any) -> None:
        cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))


class SelectedReleaseActivationAuthority:
    """Connector adapter using owner-supplied record selection and capability mapping."""

    def __init__(
        self,
        repository: ReleaseActivationRepository,
        *,
        activation_record_id: str,
    ) -> None:
        if not activation_record_id:
            raise ValueError("activation_record_id is required")
        self._repository = repository
        self._activation_record_id = activation_record_id

    def authorizes(self, request: dict[str, Any], *, evaluated_at: datetime) -> bool:
        return self._repository.capability_activated(
            self._activation_record_id,
            str(request["capability"]),
            evaluated_at=evaluated_at,
        )
