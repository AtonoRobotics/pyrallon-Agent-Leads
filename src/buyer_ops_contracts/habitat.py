"""Independent deterministic Habitat admission boundary."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from .errors import ContractViolation, Violation
from .structural import validate_record


@dataclass(frozen=True, slots=True)
class HabitatState:
    records: Mapping[str, dict[str, Any]]
    principal: dict[str, Any] | None = None
    authorization: dict[str, Any] | None = None
    workflow_reference: dict[str, Any] | None = None
    approval: dict[str, Any] | None = None
    connector_grant: dict[str, Any] | None = None
    consent: dict[str, Any] | None = None
    suppression: dict[str, Any] | None = None
    agreement_qualification: dict[str, Any] | None = None


class HabitatStateReader(Protocol):
    def load_current(self, intent: dict[str, Any]) -> HabitatState: ...


@dataclass(frozen=True, slots=True)
class PolicyDisposition:
    disposition: Literal["allowed", "prohibited", "approval_required"]
    policy_id: str
    policy_version: str


class HabitatPolicyEvaluator(Protocol):
    def evaluate(
        self,
        intent: dict[str, Any],
        state: HabitatState,
        evaluated_at: datetime,
    ) -> PolicyDisposition: ...


@dataclass(frozen=True, slots=True)
class HabitatDecision:
    allowed: bool
    reason: str
    authoritative_versions: Mapping[str, int] = field(default_factory=dict)
    policy_id: str | None = None
    policy_version: str | None = None


class HabitatKernel:
    """Deterministic admission evaluator; it never invokes a provider."""

    def __init__(
        self,
        state_reader: HabitatStateReader,
        policy_evaluator: HabitatPolicyEvaluator | None = None,
    ) -> None:
        self._state_reader = state_reader
        self._policy_evaluator = policy_evaluator

    def admit(
        self,
        intent: dict[str, Any],
        *,
        expected_tenant_id: str,
        evaluated_at: datetime,
    ) -> HabitatDecision:
        validate_effect_intent(
            intent,
            expected_tenant_id=expected_tenant_id,
            evaluated_at=evaluated_at,
        )
        state = self._state_reader.load_current(intent)
        return self.evaluate_current(
            intent,
            state=state,
            expected_tenant_id=expected_tenant_id,
            evaluated_at=evaluated_at,
        )

    def evaluate_current(
        self,
        intent: dict[str, Any],
        *,
        state: HabitatState,
        expected_tenant_id: str,
        evaluated_at: datetime,
    ) -> HabitatDecision:
        """Evaluate state that the caller reloaded under its authority/resource lock."""
        versions = {
            record_id: int(record["version"]) for record_id, record in state.records.items()
        }
        expected_versions = intent["canonical_version_vector"]
        if any(
            versions.get(record_id) != version for record_id, version in expected_versions.items()
        ):
            return HabitatDecision(False, "canonical_version_conflict", versions)
        principal = state.principal
        if (
            principal is None
            or principal.get("id") != intent["principal_id"]
            or principal.get("tenantId") != expected_tenant_id
            or principal.get("recordType") != "ServicePrincipal"
            or principal.get("status") != "active"
            or principal.get("principalState") != "active"
        ):
            return HabitatDecision(False, "identity_invalid", versions)
        authorization = state.authorization
        if authorization is None:
            return HabitatDecision(False, "authority_missing", versions)
        if authorization.get("authorizationState") == "revoked" or "revokedAt" in authorization:
            return HabitatDecision(False, "authority_revoked", versions)
        target = intent["target_resource"]
        if (
            authorization.get("status") != "active"
            or authorization.get("authorizationState") != "active"
            or authorization.get("granteeId") != intent["principal_id"]
            or authorization.get("actionClass") != intent["action_class"]
            or authorization.get("resourceType") != target["resource_type"]
            or authorization.get("resourceId") != target["resource_id"]
            or _timestamp(authorization["grantedAt"]) > evaluated_at.astimezone(UTC)
            or _timestamp(authorization["expiresAt"]) <= evaluated_at.astimezone(UTC)
        ):
            return HabitatDecision(False, "authority_missing", versions)
        workflow_reference = state.workflow_reference
        if (
            workflow_reference is None
            or workflow_reference.get("status") != "active"
            or workflow_reference.get("workflowId") != intent["workflow_id"]
            or workflow_reference.get("subjectId") != intent["buyer_journey_id"]
            or workflow_reference.get("executionState") != "running"
        ):
            return HabitatDecision(False, "concurrency_conflict", versions)
        if self._policy_evaluator is None:
            return HabitatDecision(False, "policy_denied", versions)
        policy = self._policy_evaluator.evaluate(intent, state, evaluated_at)
        if policy.disposition == "prohibited":
            return HabitatDecision(
                False,
                "policy_denied",
                versions,
                policy.policy_id,
                policy.policy_version,
            )
        if policy.disposition == "approval_required" and "approval_ref" not in intent:
            return HabitatDecision(
                False,
                "approval_required",
                versions,
                policy.policy_id,
                policy.policy_version,
            )
        if "approval_ref" in intent:
            approval = state.approval
            if approval is None or approval.get("id") != intent["approval_ref"]:
                return HabitatDecision(
                    False, "approval_required", versions, policy.policy_id, policy.policy_version
                )
            if _timestamp(approval["expiresAt"]) <= evaluated_at.astimezone(UTC):
                return HabitatDecision(
                    False, "approval_expired", versions, policy.policy_id, policy.policy_version
                )
            if approval.get("decision") != "approved":
                return HabitatDecision(
                    False, "approval_required", versions, policy.policy_id, policy.policy_version
                )
            if (
                approval.get("actionClass") != intent["action_class"]
                or approval.get("actionIntentId") != intent["intent_id"]
                or approval.get("payloadDigest") != intent["approved_digest"]
                or approval.get("payloadDigest") != intent["payload_digest"]
            ):
                return HabitatDecision(
                    False, "payload_changed", versions, policy.policy_id, policy.policy_version
                )
        connector = state.connector_grant
        if (
            connector is None
            or connector.get("state") != "active"
            or connector.get("connectorBindingId") != intent["connector_binding_id"]
            or connector.get("principalId") != intent["principal_id"]
            or intent["action_class"] not in connector.get("actionClasses", [])
        ):
            return HabitatDecision(
                False, "connector_unavailable", versions, policy.policy_id, policy.policy_version
            )
        if connector.get("requiresConsent"):
            recipient_id = intent["recipient"]["recipient_id"]
            suppression = state.suppression
            if (
                suppression is not None
                and suppression.get("validityState") == "active"
                and suppression.get("subjectId") == recipient_id
                and _timestamp(suppression["suppressedAt"]) <= evaluated_at.astimezone(UTC)
            ):
                return HabitatDecision(
                    False, "consent_denied", versions, policy.policy_id, policy.policy_version
                )
            consent = state.consent
            if (
                consent is None
                or consent.get("validityState") != "active"
                or consent.get("personId") != recipient_id
                or consent.get("principalId") != intent["principal_id"]
                or consent.get("channel") != connector.get("channel")
                or consent.get("purpose") != intent["purpose"]
                or _timestamp(consent["grantedAt"]) > evaluated_at.astimezone(UTC)
                or (
                    "expiresAt" in consent
                    and _timestamp(consent["expiresAt"]) <= evaluated_at.astimezone(UTC)
                )
            ):
                return HabitatDecision(
                    False, "consent_denied", versions, policy.policy_id, policy.policy_version
                )
        if intent["action_class"] in {
            "residential_showing",
            "residential_offer_presentation",
        }:
            qualification = state.agreement_qualification
            if (
                qualification is None
                or qualification.get("result") != "qualified"
                or qualification.get("actionType") != intent["action_class"]
                or qualification.get("actionIntentId") != intent["intent_id"]
                or qualification.get("actionPayloadDigest") != intent["payload_digest"]
                or _timestamp(qualification["expiresAt"]) <= evaluated_at.astimezone(UTC)
            ):
                return HabitatDecision(
                    False,
                    "representation_conflict",
                    versions,
                    policy.policy_id,
                    policy.policy_version,
                )
        return HabitatDecision(True, "admitted", versions, policy.policy_id, policy.policy_version)


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def validate_effect_intent(
    intent: dict[str, Any],
    *,
    expected_tenant_id: str,
    evaluated_at: datetime | None = None,
) -> None:
    """Fail closed on unknown EffectIntent schema or tenant before authority evaluation."""
    validate_record(intent, "habitat")
    if intent["tenant_id"] != expected_tenant_id:
        raise ContractViolation(
            [
                Violation(
                    "TENANT_ADMISSION",
                    "$.tenant_id",
                    "EffectIntent tenant does not match the authenticated tenant",
                )
            ]
        )
    target = intent["target_resource"]
    if intent["canonical_version_vector"].get(target["resource_id"]) != target["version"]:
        raise ContractViolation(
            [
                Violation(
                    "TARGET_VERSION_BINDING",
                    "$.canonical_version_vector",
                    "target resource version must be exactly bound in the canonical version vector",
                )
            ]
        )
    if evaluated_at is not None:
        if evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must include a timezone")
        expiry = datetime.fromisoformat(intent["proposal_expires_at"].replace("Z", "+00:00"))
        if expiry.astimezone(UTC) <= evaluated_at.astimezone(UTC):
            raise ContractViolation(
                [
                    Violation(
                        "PROPOSAL_EXPIRED",
                        "$.proposal_expires_at",
                        "proposal is not current at Habitat evaluation",
                    )
                ]
            )
