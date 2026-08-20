"""Provider-neutral connector gateway. Permit redemption is required; live invoke is activation-gated."""

from __future__ import annotations

from typing import Any, Protocol

from .structural import validate_record


class ConnectorDenied(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


class ConnectorRepository(Protocol):
    def get(self, record_id: str) -> dict[str, Any] | None: ...

    def list_by_type(self, record_type: str) -> list[dict[str, Any]]: ...


class ConnectorGateway:
    def __init__(
        self,
        repository: ConnectorRepository,
        *,
        tenant_id: str,
    ) -> None:
        self._repository = repository
        self._tenant_id = tenant_id

    def inventory(self) -> list[dict[str, Any]]:
        grants = self._repository.list_by_type("ConnectorGrant")
        rows = []
        for grant in grants:
            rows.append(
                {
                    "connector_id": grant.get("connectorId"),
                    "grant_id": grant["id"],
                    "grant_state": grant.get("grantState"),
                    "delegated_principal_id": grant.get("delegatedPrincipalId"),
                    "capabilities": grant.get("capabilities"),
                    "scopes": grant.get("scopes"),
                }
            )
        return rows

    def invoke(self, request: dict[str, Any], *, permit_digest: str) -> dict[str, Any]:
        validate_record(request, "connector_gateway")
        if request.get("messageType") != "connector_request":
            raise ConnectorDenied("validation_failed", "not a connector_request")
        if request["tenantId"] != self._tenant_id:
            raise ConnectorDenied("authority_denied", "tenant mismatch")
        if not permit_digest:
            raise ConnectorDenied("authority_denied", "permit digest required")
        grant = self._repository.get(str(request["grantId"]))
        if grant is None or grant.get("recordType") != "ConnectorGrant":
            raise ConnectorDenied("connector_revoked", "connector grant missing")
        if grant.get("grantState") != "active":
            raise ConnectorDenied("connector_revoked", "connector grant is not active")
        if int(grant["version"]) != int(request["grantVersion"]):
            raise ConnectorDenied("version_conflict", "grant version mismatch")
        if request["capability"] not in [str(item) for item in grant.get("capabilities", [])]:
            raise ConnectorDenied("authority_denied", "grant lacks required capability")
        raise ConnectorDenied(
            "configuration_incomplete",
            "live provider adapters are not activated for this environment",
        )
