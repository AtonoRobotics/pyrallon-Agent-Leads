"""Fail-closed boundary for operator-surface/1.1.0 JourneyView projections."""

from __future__ import annotations

from typing import Any

from .canonical_repository import CanonicalRepository


class OperatorProjection:
    """Operator projection remains unavailable until its derivation contract is published."""

    def __init__(
        self,
        repository: CanonicalRepository,
        *,
        tenant_id: str,
    ) -> None:
        self._repository = repository
        self._tenant_id = tenant_id

    def journey_view(self, *, journey_id: str, principal_id: str) -> dict[str, Any]:
        journey = self._repository.get(journey_id)
        if journey is None or journey.get("recordType") != "BuyerJourney":
            raise KeyError("BuyerJourney not found")
        if journey.get("tenantId") != self._tenant_id:
            raise PermissionError("BuyerJourney tenant mismatch")
        del principal_id
        raise KeyError("governed JourneyView projection rules are unavailable")

    def list_journey_ids(self) -> list[str]:
        return [item["id"] for item in self._repository.list_by_type("BuyerJourney")]
