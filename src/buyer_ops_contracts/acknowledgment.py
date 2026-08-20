"""Deterministic OPEN-027 opt-out and acknowledgment contract semantics."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any

from .structural import validate_record

_TOKEN = re.compile(rb"\{\{([A-Za-z0-9_]+)\}\}")


def normalize_opt_out_text(value: str) -> str:
    """Apply the only normalization algorithm admitted by OT01 1.1."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def validate_acknowledgment_config(record: dict[str, Any]) -> None:
    validate_record(record, "ot01_ingress")
    starts = datetime.fromisoformat(record["effectiveFrom"].replace("Z", "+00:00"))
    ends = (
        None
        if "effectiveTo" not in record
        else datetime.fromisoformat(record["effectiveTo"].replace("Z", "+00:00"))
    )
    if ends is not None and ends <= starts:
        raise ValueError("configuration effectiveTo must follow effectiveFrom")
    if record["recordVersion"] == 1 and "supersedesRecordId" in record:
        raise ValueError("initial configuration cannot supersede another version")
    if record["recordVersion"] > 1 and record.get("supersedesRecordId") not in {
        record.get("policyId"),
        record.get("lexiconId"),
    }:
        raise ValueError("configuration successor must bind its stable predecessor identity")
    if record["messageType"] == "opt_out_lexicon":
        normalized = [normalize_opt_out_text(item) for item in record["expressions"]]
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("opt-out expressions must be nonempty and unique after normalization")
    elif record["messageType"] == "acknowledgment_policy":
        rule_ids = [rule["ruleId"] for rule in record["rules"]]
        if len(rule_ids) != len(set(rule_ids)) or set(record["selectionOrder"]) != set(rule_ids):
            raise ValueError("selectionOrder must name each rule exactly once in evaluation order")
    else:
        raise ValueError("record is not acknowledgment configuration")


def _reference_matches(reference: dict[str, Any], record: dict[str, Any], kind: str) -> bool:
    identifier = "policyId" if kind == "AcknowledgmentPolicy" else "lexiconId"
    return (
        reference
        == {
            "recordId": record[identifier],
            "recordType": kind,
            "version": record["recordVersion"],
            "status": record["status"],
        }
        and record["status"] == "active"
    )


def _matches_lexicon(raw_text: str, lexicon: dict[str, Any]) -> str | None:
    normalized = normalize_opt_out_text(raw_text)
    for expression in lexicon["expressions"]:
        candidate = normalize_opt_out_text(expression)
        if normalized == candidate or (
            lexicon["matchMode"] == "leading_token" and normalized.startswith(candidate + " ")
        ):
            return candidate
    return None


def build_acknowledgment_decision(
    request: dict[str, Any],
    policy: dict[str, Any],
    lexicon: dict[str, Any],
    raw_inbound_text: str,
    template_bytes: bytes,
) -> dict[str, Any]:
    """Evaluate immutable owner configuration without choosing any hidden default."""
    validate_record(request, "ot01_ingress")
    captured = datetime.fromisoformat(request["capturedAt"].replace("Z", "+00:00"))
    requested = datetime.fromisoformat(request["requestedAt"].replace("Z", "+00:00"))
    if requested < captured:
        raise ValueError("requestedAt cannot precede capturedAt")
    validate_acknowledgment_config(policy)
    validate_acknowledgment_config(lexicon)
    if not _reference_matches(request["acknowledgmentPolicyRef"], policy, "AcknowledgmentPolicy"):
        raise ValueError("acknowledgment policy reference is not the active exact version")
    if not _reference_matches(request["optOutLexiconRef"], lexicon, "OptOutLexicon"):
        raise ValueError("opt-out lexicon reference is not the active exact version")
    if request["channel"] not in lexicon["channels"] or request["locale"] != lexicon["locale"]:
        return configuration_incomplete_decision(request)
    matched_expression = _matches_lexicon(raw_inbound_text, lexicon)
    opt_out_state = "matched" if matched_expression is not None else "not_matched"
    rules = {rule["ruleId"]: rule for rule in policy["rules"]}
    selected = next(
        (
            rules[rule_id]
            for rule_id in policy["selectionOrder"]
            if request["channel"] in rules[rule_id]["channels"]
            and request["locale"] in rules[rule_id]["locales"]
            and request["operatingHourState"] in rules[rule_id]["operatingHourStates"]
            and request["contactabilityState"] in rules[rule_id]["contactabilityStates"]
            and request["inboundPurpose"] in rules[rule_id]["inboundPurposes"]
            and opt_out_state in rules[rule_id]["optOutStates"]
        ),
        None,
    )
    base: dict[str, Any] = {
        "messageType": "acknowledgment_decision",
        "schemaVersion": "ot01-ingress/1.1.0",
        "decisionId": request["decisionId"],
        "requestId": request["requestId"],
        "tenantId": request["tenantId"],
        "externalMessageIdentityRef": request["externalMessageIdentityRef"],
        "capturedAt": request["capturedAt"],
        "policyRef": request["acknowledgmentPolicyRef"],
        "lexiconRef": request["optOutLexiconRef"],
        "optOutMatched": matched_expression is not None,
        "decidedAt": request["requestedAt"],
        "sourceEvidenceIds": request["sourceEvidenceIds"],
    }
    if selected is None:
        base["disposition"] = policy["noMatchDisposition"]
        validate_record(base, "ot01_ingress")
        return base
    digest = f"sha256:{hashlib.sha256(template_bytes).hexdigest()}"
    if digest != selected["templateDigest"]:
        raise ValueError("template digest does not match the selected immutable artifact")
    placeholders = {item.decode() for item in _TOKEN.findall(template_bytes)}
    substitutions = request["substitutions"]
    if placeholders != set(substitutions) or not placeholders.issubset(
        set(selected["allowedSubstitutionKeys"])
    ):
        raise ValueError("substitution keys must exactly match declared template placeholders")
    rendered = template_bytes
    for key in sorted(placeholders):
        rendered = rendered.replace(b"{{" + key.encode() + b"}}", substitutions[key].encode())
    base.update(
        {
            "disposition": "suppress_and_acknowledge" if matched_expression else "send_template",
            "selectedRuleId": selected["ruleId"],
            "templateArtifactId": selected["templateArtifactId"],
            "templateVersion": selected["templateVersion"],
            "templateDigest": digest,
            "senderPrincipalId": selected["senderPrincipalId"],
            "senderEndpointId": selected["senderEndpointId"],
            "recipientEndpointId": request["recipientEndpointId"],
            "effectPurpose": selected["effectPurpose"],
            "normalizedPayloadDigest": f"sha256:{hashlib.sha256(rendered).hexdigest()}",
            "idempotencyKey": request["idempotencyKey"],
            "failureDisposition": policy["failureDisposition"],
            "expiresAt": (
                datetime.fromisoformat(request["requestedAt"].replace("Z", "+00:00"))
                + timedelta(seconds=selected["expiresInSeconds"])
            )
            .isoformat()
            .replace("+00:00", "Z"),
        }
    )
    if matched_expression is not None:
        candidate = request.get("suppressionRecordCandidate")
        if not isinstance(candidate, dict):
            raise ValueError("opt-out requires suppressionRecordCandidate")
        validate_record(candidate, "ontology")
        if (
            candidate.get("recordType") != "Suppression"
            or candidate.get("tenantId") != request["tenantId"]
            or candidate.get("subjectId") != request["subjectId"]
            or candidate.get("reason") != "opt_out"
            or candidate.get("scope") != lexicon["suppressionScope"]
            or candidate.get("endpointId") != request["recipientEndpointId"]
            or candidate.get("suppressedAt") != request["requestedAt"]
            or candidate.get("status") != "active"
            or candidate.get("validityState") != "active"
            or not set(request["sourceEvidenceIds"]).issubset(
                candidate.get("sourceEvidenceIds", [])
            )
        ):
            raise ValueError("suppressionRecordCandidate is not bound to the opt-out decision")
        base["matchedExpressionDigest"] = (
            f"sha256:{hashlib.sha256(matched_expression.encode()).hexdigest()}"
        )
        base["suppressionRef"] = {
            "recordId": candidate["id"],
            "recordType": "Suppression",
            "version": candidate["version"],
            "status": candidate["status"],
        }
    validate_record(base, "ot01_ingress")
    return base


def configuration_incomplete_decision(request: dict[str, Any]) -> dict[str, Any]:
    """Return the governed fail-closed result for missing or stale configuration."""
    decision = {
        "messageType": "acknowledgment_decision",
        "schemaVersion": "ot01-ingress/1.1.0",
        "decisionId": request["decisionId"],
        "requestId": request["requestId"],
        "tenantId": request["tenantId"],
        "externalMessageIdentityRef": request["externalMessageIdentityRef"],
        "capturedAt": request["capturedAt"],
        "disposition": "configuration_incomplete",
        "policyRef": request["acknowledgmentPolicyRef"],
        "lexiconRef": request["optOutLexiconRef"],
        "optOutMatched": False,
        "decidedAt": request["requestedAt"],
        "sourceEvidenceIds": request["sourceEvidenceIds"],
    }
    validate_record(decision, "ot01_ingress")
    return decision
