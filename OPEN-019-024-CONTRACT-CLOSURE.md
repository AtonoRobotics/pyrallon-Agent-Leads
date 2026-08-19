# OPEN-019–024 Governing Contract Closure

**Revision:** 1.1.0  
**Status:** governing  
**Applies to:** kernel, operator surface, connector gateway, context compiler, telemetry, release evidence, and accessibility acceptance

This contract closes OPEN-019 through OPEN-024 without adding a second CRM or changing the canonical ontology. All records are tenant-scoped, versioned, evidence-linked, and rejected when their governing version is unsupported.

## Common lifecycle and evidence binding

Every closure record carries `recordVersion`, `effectiveFrom`, lifecycle `status`, and non-empty `evidenceRefs`. Versions after one identify `supersedesRecordId`. Non-current records carry `effectiveTo`; an expiry, when present, follows `effectiveFrom`. For each tenant and governed business identity, at most one record may be current. Consumers reject unknown schema versions and ambiguous current records.

## OPEN-019 — stable external message identity

Every inbound provider envelope MUST carry `ExternalMessageIdentity` before persistence:

```json
{
  "connectorId": "gmail-primary",
  "provider": "gmail",
  "providerAccountRef": "account-1",
  "externalMessageId": "provider-message-id",
  "externalEventId": "webhook-event-id",
  "payloadDigest": "sha256:..."
}
```

The durable deduplication key is `(tenantId, connectorId, providerAccountRef, externalMessageId)`. `externalEventId` is an event trace key and MUST NOT be used as the message identity. A repeated message with a different event ID is the same message when the stable key and payload digest agree. A stable key with a different digest enters `reconciliation_required`; it never overwrites the original.

## OPEN-020 — connector capability and draft contracts

Each configured connector MUST publish a signed, versioned `CapabilityInventory` before scheduling work. Each proposed provider-changing action MUST first produce an `EffectDraftPreview` containing the normalized payload digest, capability, recipients/resources, requested window, required authority class, and whether the action is reversible. Provider invocation remains impossible without a current Habitat `EffectPermit` whose payload digest equals the preview digest.

An inventory signature is Ed25519 over RFC 8785 canonical JSON of the record with `signature` removed; `inventoryDigest` is SHA-256 over the same bytes with both `signature` and `inventoryDigest` removed. The signing key is identified by `signature.keyId`. `capabilityEffects` maps each effect-bearing capability exactly once to its admitted action classes and a governed constraint digest. A draft binds the exact inventory record/version/digest, connector grant/version, delegated principal, payload canonicalization version, idempotency key, targets, and recipients.

Capability changes invalidate scheduled work that depends on the removed or narrowed capability. Drafts are not provider effects and do not imply delivery.

## OPEN-021 — context freshness and output routing

Every context source MUST include `observedAt`, `freshnessAt`, `freshUntil`, and `epistemicType`. The context compiler MUST reject a source after `freshUntil` unless the action-class contract explicitly permits stale evidence and labels it stale.

Every action class MUST map to exactly one `outputClass` in the versioned route policy. An output class defines allowed response artifacts, grounding requirements, authority requirements, and whether a provider effect is eligible. Unknown action/output mappings fail closed.

`OutputClassMapping` declares `allowedArtifactTypes`, `requiredAuthorityClasses`, `allowedEpistemicTypes`, and `staleEvidencePolicy`. `allow_labeled` requires `staleLabel`; `reject` forbids one. The selected mapping record ID/version is bound into the context manifest. A tenant/action/policy version with zero or multiple current mappings fails closed.

## OPEN-022 — ratio telemetry

Ratio metrics MUST be declared as `MetricDefinition` records. Each definition names an immutable numerator event, denominator event, matching correlation key, dimensions, aggregation window, minimum denominator, and division-by-zero behavior. Observations MUST retain the numerator and denominator counts and their event-set digests; a bare ratio is not evidence.

Event-set digests are SHA-256 over RFC 8785 canonical JSON arrays of event identities sorted lexicographically. An observation binds the definition record/version, declared event identities, correlation key/digest, window, and exact dimension keys. Numerator event identities are a subset of denominator identities for conversion-style ratios. Below the minimum denominator the state is `unknown`; at zero it follows `zeroDenominatorBehavior`. Only `value` observations carry a numeric value.

## OPEN-023 — release evidence and gate applicability

Every release gate MUST declare `applicability` (`platform_invariant`, `operational_thread`, `capability`, or `complete_product`). A `ReleaseEvidence` record MUST state the gate, applicable scope, test/evaluation version, outcome (`pass`, `fail`, `blocked`, or `not_applicable`), evidence references, observedAt, expiresAt, and owner. `not_applicable` is valid only when the gate registry proves the capability is disabled; it cannot be used to bypass a platform invariant.

Evidence binds the gate-registry version/digest and tested release digest. Capability gates name `capabilityId`. A `not_applicable` outcome requires capability scope plus non-empty `disabledCapabilityEvidenceRefs`. Activation requires one unexpired current `pass` record for every gate applicable to the release; legacy evidence without an explicit passing outcome never satisfies activation.

## OPEN-024 — accessibility evidence lifecycle

Accessibility evidence MUST identify WCAG 2.2 AA, test suite/version, target surface, tested build digest, assistive technologies, known exceptions, outcome, owner, observedAt, and expiresAt. Evidence states are `current`, `expired`, `superseded`, `failed`, or `waived`. Only `current` evidence may satisfy release acceptance. A waiver requires a recorded scope, reason, compensating control, expiry, and approving authority; it cannot waive keyboard access, focus visibility, semantic names, or equivalent WCAG obligations without an explicit legal basis.

Waivers encode `waiverScope`, `affectedObligations`, `compensatingControl`, `waiverApproverAuthorityClass`, and `waiverApproverId`. Protected obligation identifiers are `keyboard_access`, `focus_visibility`, `semantic_names`, and `equivalent_wcag_obligations`; a waiver touching one also requires `legalBasis`. Accessibility acceptance binds both deployed build and release digests.

## Migration reconciliation

Migration `0009_control_plane.sql` is part of the governed migration sequence. Rollback refusal is required from `0008` onward; `0009` MUST have forward-apply, duplicate-apply, restart, and refusal evidence. The completion ledger MUST reference the same highest applied migration and digest.

## Closure acceptance

The blockers are closed only when the JSON Schema validates these record types, the kernel persists and enforces the identity/draft/permit rules, the context compiler enforces freshness and output routing, telemetry emits bound numerator/denominator observations, release evidence evaluates gate applicability, accessibility evidence executes against the deployed build, and migration `0009` is reconstructed from a clean database.

