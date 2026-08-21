import copy
import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import rfc8785

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tests" / "fixtures" / "generated"


def base(record_type: str) -> dict[str, Any]:
    return {
        "schemaVersion": "open-019-024/1.1.0",
        "tenantId": "tenant-1",
        "recordId": record_type.lower(),
        "recordVersion": 1,
        "observedAt": "2030-01-01T00:00:00Z",
        "effectiveFrom": "2030-01-01T00:00:00Z",
        "status": "current",
        "evidenceRefs": ["evidence-1"],
        "recordType": record_type,
    }


def records() -> dict[str, dict[str, Any]]:
    digest_a = "sha256:" + "a" * 64
    digest_b = "sha256:" + "b" * 64
    digest_c = "sha256:" + "c" * 64
    binding = base("AccessibilityBinding") | {
        "expiresAt": "2030-02-01T00:00:00Z",
        "operatorAcceptanceRecordId": "acceptance-web",
        "operatorAcceptanceDigest": digest_c,
        "closureEvidenceRecordId": "accessibilityevidence",
        "closureEvidenceDigest": digest_a,
        "surface": "web",
        "buildDigest": digest_a,
        "releaseDigest": digest_b,
    }
    binding["bindingDigest"] = (
        "sha256:"
        + sha256(
            rfc8785.dumps(
                {
                    key: binding[key]
                    for key in (
                        "tenantId",
                        "recordId",
                        "recordVersion",
                        "operatorAcceptanceRecordId",
                        "operatorAcceptanceDigest",
                        "closureEvidenceRecordId",
                        "closureEvidenceDigest",
                        "surface",
                        "buildDigest",
                        "releaseDigest",
                        "expiresAt",
                    )
                }
            )
        ).hexdigest()
    )
    return {
        "ExternalMessageIdentity": base("ExternalMessageIdentity")
        | {
            "connectorId": "connector-1",
            "provider": "provider-1",
            "providerAccountRef": "account-1",
            "externalMessageId": "message-1",
            "externalEventId": "event-1",
            "payloadDigest": digest_a,
        },
        "CapabilityInventory": base("CapabilityInventory")
        | {
            "expiresAt": "2030-02-01T00:00:00Z",
            "connectorId": "connector-1",
            "connectorVersion": "1.0.0",
            "capabilities": ["read", "send"],
            "effectClasses": ["send_message"],
            "capabilityEffects": [
                {
                    "capability": "send",
                    "actionClasses": ["send_message"],
                    "constraintDigest": digest_b,
                }
            ],
            "canonicalizationVersion": "jcs-rfc8785/1",
            "inventoryDigest": digest_a,
            "signature": {"algorithm": "Ed25519", "keyId": "key-1", "value": "signature"},
        },
        "EffectDraftPreview": base("EffectDraftPreview")
        | {
            "connectorId": "connector-1",
            "inventoryRecordId": "capabilityinventory",
            "inventoryRecordVersion": 1,
            "inventoryDigest": digest_a,
            "grantId": "grant-1",
            "grantVersion": 1,
            "delegatedPrincipalId": "principal-1",
            "capability": "send",
            "actionClass": "send_message",
            "payloadCanonicalizationVersion": "message/1",
            "payloadDigest": digest_b,
            "idempotencyKey": "send-1",
            "targetRefs": ["conversation-1"],
            "recipientRefs": ["endpoint-1"],
            "requestedExecutionWindow": {
                "notBefore": "2030-01-01T00:00:00Z",
                "expiresAt": "2030-01-01T00:05:00Z",
            },
            "authorityClass": "effect",
            "reversible": False,
        },
        "ContextSourceFreshness": base("ContextSourceFreshness")
        | {
            "sourceRecordId": "person-1",
            "epistemicType": "verified_fact",
            "freshnessAt": "2030-01-01T00:00:00Z",
            "freshUntil": "2030-01-02T00:00:00Z",
        },
        "OutputClassMapping": base("OutputClassMapping")
        | {
            "expiresAt": "2030-02-01T00:00:00Z",
            "actionClass": "lead_qualification",
            "outputClass": "qualification_advice",
            "policyVersion": "route-policy/1",
            "allowedArtifactTypes": ["qualification_summary"],
            "requiredAuthorityClasses": ["read_buyer_context"],
            "allowedEpistemicTypes": ["verified_fact"],
            "staleEvidencePolicy": "reject",
            "groundingMode": "all_claims_grounded",
            "effectEligibility": "none",
        },
        "EffectPolicy": base("EffectPolicy")
        | {
            "expiresAt": "2030-02-01T00:00:00Z",
            "policyId": "effect-policy-1",
            "policyVersion": "effect-policy/1",
            "rules": [{"actionClass": "send_message", "disposition": "allowed"}],
        },
        "MetricDefinition": base("MetricDefinition")
        | {
            "metricId": "qualification_completion_ratio",
            "unit": "ratio",
            "numeratorEvent": "qualification_completed",
            "denominatorEvent": "qualification_started",
            "correlationKey": "journey_id",
            "dimensions": ["channel"],
            "window": "rolling_24h",
            "minimumDenominator": 1,
            "zeroDenominatorBehavior": "unknown",
        },
        "MetricObservation": base("MetricObservation")
        | {
            "metricId": "qualification_completion_ratio",
            "metricDefinitionRecordId": "metricdefinition",
            "metricDefinitionRecordVersion": 1,
            "window": "rolling_24h",
            "windowStart": "2030-01-01T00:00:00Z",
            "windowEnd": "2030-01-01T01:00:00Z",
            "numerator": 1,
            "denominator": 2,
            "numeratorEvent": "qualification_completed",
            "denominatorEvent": "qualification_started",
            "correlationKey": "journey_id",
            "correlationDigest": digest_a,
            "numeratorEventDigest": digest_b,
            "denominatorEventDigest": digest_c,
            "dimensionValues": {"channel": "web"},
            "calculationState": "value",
            "value": 0.5,
        },
        "ReleaseEvidence": base("ReleaseEvidence")
        | {
            "expiresAt": "2030-02-01T00:00:00Z",
            "gateId": "GATE-002",
            "gateRegistryVersion": "1.0.0",
            "gateRegistryDigest": digest_a,
            "applicability": "platform_invariant",
            "scope": "all_live_effects",
            "releaseDigest": digest_b,
            "testVersion": "fault-suite/1",
            "outcome": "pass",
            "ownerId": "platform-operations",
        },
        "AccessibilityEvidence": base("AccessibilityEvidence")
        | {
            "expiresAt": "2030-02-01T00:00:00Z",
            "standard": "WCAG 2.2 AA",
            "suiteVersion": "a11y-suite/1",
            "surface": "web",
            "buildDigest": digest_a,
            "releaseDigest": digest_b,
            "assistiveTechnologies": ["keyboard", "screen-reader"],
            "knownExceptions": [],
            "outcome": "current",
            "ownerId": "accessibility-owner",
        },
        "AccessibilityBinding": binding,
    }


def main() -> None:
    valid = records()
    removed_fields = {
        "ExternalMessageIdentity": "externalMessageId",
        "CapabilityInventory": "signature",
        "EffectDraftPreview": "inventoryDigest",
        "ContextSourceFreshness": "freshUntil",
        "OutputClassMapping": "allowedArtifactTypes",
        "EffectPolicy": "rules",
        "MetricDefinition": "correlationKey",
        "MetricObservation": "denominatorEventDigest",
        "ReleaseEvidence": "gateRegistryDigest",
        "AccessibilityEvidence": "buildDigest",
        "AccessibilityBinding": "bindingDigest",
    }
    invalid = {}
    for name, record in valid.items():
        broken = copy.deepcopy(record)
        removed = removed_fields[name]
        broken.pop(removed)
        invalid[name] = {"removedField": removed, "record": broken}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "closure_1_1_valid.json").write_text(json.dumps(valid, indent=2) + "\n")
    (OUTPUT / "closure_1_1_invalid.json").write_text(json.dumps(invalid, indent=2) + "\n")


if __name__ == "__main__":
    main()
