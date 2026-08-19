# OPEN-025–027 Governing Contract Closure

**Revision:** 1.0.0  
**Status:** governing  
**Authority:** specification owner  
**Applies to:** canonical authorization, operator control plane, release activation, connector gateway, and GATE-007 fair-housing controls

This contract closes OPEN-025 through OPEN-027. It replaces provisional local semantics; implementation is conforming only after it consumes the hash-pinned schema and passes the acceptance rules below.

## OPEN-025 — canonical actor/tenant authorization

`ActorTenantAuthorization` is the sole operator-surface source for tenant membership and command authority. It binds an authenticated `actorId` to a `principalId`, `tenantId`, role, allowed commands, record scopes, policy version, and temporal validity. The client never supplies an authoritative tenant or widens scopes.

Rules:

- no current authorization produces an empty console and denied commands, never a demo tenant;
- tenant selection is derived server-side from current canonical authorizations;
- every operator command is tenant-bound, idempotent, and conditional on the expected canonical version;
- revocation or expiry is effective before the next command/effect admission;
- multiple tenant grants remain separate and require an explicit authorized selection;
- authorization records are append-only/superseding; no client-local role is governing.

## OPEN-026 — signed release and capability activation

`ReleaseActivation` binds one environment, release, build digest, contract-manifest digest, policy version, enabled capability set, required gate set, current evidence, signer, signature, and validity interval. Activation is capability-scoped: an activated release does not activate an omitted connector, cognitive route, channel, or workflow.

Rules:

- every required platform-invariant gate and every applicable thread/capability gate is present with current `pass` evidence;
- `not_applicable` cannot satisfy a platform invariant or a gate for an enabled capability;
- the signer must have the declared activation authority under OPEN-025;
- build, manifest, policy, capability, gate-evidence, signer, expiry, or revocation changes invalidate readback;
- the connector gateway rejects provider-changing calls unless both the capability activation and a current single-use Habitat permit are valid;
- activation readback returns the exact signed payload and verification result; a boolean alone is insufficient;
- voice origination remains prohibited and cannot appear in `enabledCapabilities`.

## OPEN-027 — deterministic fair-housing feature and counterfactual controls

`FairHousingControlProfile` governs the GATE-007 compiler. It declares the normalization algorithm, protected terms and phrases, allowed operational features with purpose bounds, prohibited proxy rules, immutable service guarantees, optimizer bounds, and promotion criteria. `FairHousingCounterfactualCase` declares paired inputs that differ only in a protected-trait reference and the outcomes that must remain invariant.

Matching rules:

- normalize with Unicode NFKC, Unicode case-folding, and whitespace normalization;
- match declared tokens on Unicode token boundaries and declared multi-token phrases on normalized phrase boundaries;
- substring matching is prohibited (`Sussex` MUST NOT match `sex`);
- free text, names, ZIP codes, schools, income, and geography are unavailable to optimization unless a versioned allowlist entry states a buyer-originated property/operational purpose;
- an allowlist permits only its named purpose and action classes; it does not make a feature generally safe;
- protected traits and prohibited proxies cannot alter eligibility, suppression, service level, response SLO, cadence, escalation, appointment availability, or ranking;
- deterministic counterfactual outcomes must be identical for all invariant fields;
- statistical promotion requires a preregistered slice, metric, minimum sample, parity threshold, confidence method, rollback trigger, and evidence. No default threshold may be invented by code;
- minimum service guarantees, quiet hours, and maximum frequency are immutable optimizer inputs.

Lexicons are defense-in-depth, not a complete proxy detector. Undeclared features and unclassified free text fail closed.

## Acceptance and migration

OPEN-025 closes when cross-tenant, revoked, expired, stale-version, duplicate-command, and no-authorization tests pass. OPEN-026 closes when signature, digest mutation, evidence expiry, capability omission, permit conjunction, and prohibition tests pass. OPEN-027 closes when Unicode normalization, token/phrase boundaries, `Sussex` negative matching, purpose-bound feature, counterfactual invariance, service-floor immutability, and promotion-policy tests pass.

Existing local implementations are provisional until contract synchronization records the exact schema digest and the completion ledger cites passing evidence. These contracts define semantics; they do not activate any production capability by themselves.
