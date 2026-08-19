# OT-01 Ingress Contract 1.1

`OT01-INGRESS.schema.json` is authoritative for attribution, consent-presentation evidence,
deterministic opt-out recognition, acknowledgment selection, and attributable acknowledgment
outcomes. It closes OPEN-027 without supplying brokerage policy values.

## Configuration ownership

An active `OptOutLexicon` is supplied by the brokerage/channel policy owner. It declares channels,
locale, exact expressions, normalization algorithm, match mode, and resulting ontology suppression
scope. An active `AcknowledgmentPolicy` is supplied by the brokerage communications owner. It
declares an explicit total rule order, every selection dimension, immutable template artifact,
template version and digest, sender identity, permitted substitutions, effect purpose, expiry, and
failure/no-match dispositions. Missing, stale, ambiguous, or non-current configuration produces
`configuration_incomplete`; the runtime supplies no default phrase, template, sender, purpose,
expiry, or fallback.

Configuration identity is stable across versions. Version 1 has no predecessor; every later
version advances by exactly one and names the same stable `policyId` or `lexiconId` in
`supersedesRecordId`. A newly admitted version replaces the current projection without rewriting
history. `effectiveTo`, when present, is strictly after `effectiveFrom`; evaluation uses the
half-open interval `[effectiveFrom,effectiveTo)`. Draft configuration is not executable. Active may
be succeeded or retired; superseded and retired versions are audit-only.

## Deterministic opt-out recognition

`unicode_nfkc_casefold_trim_collapse_whitespace` performs Unicode NFKC normalization, case folding,
trimming, and replacement of each non-empty whitespace run with one ASCII space. Lexicon expressions
are normalized by the same algorithm and must remain unique and non-empty.

- `exact_normalized` matches only when the complete normalized inbound text equals an expression.
- `leading_token` matches when it equals an expression or begins with that expression followed by
  one ASCII space.

No fuzzy, semantic, model-based, substring, or untranslated match is permitted. If a match occurs,
the request must carry a complete ontology `Suppression` candidate whose tenant, subject/endpoint,
scope, reason `opt_out`, effective time, source evidence, and creator are already supplied. The
service validates and commits that record before an acknowledgment decision can be admitted.

## Deterministic template selection

Rules are evaluated only in `selectionOrder`; that array must contain every rule ID exactly once.
A rule matches exact membership for channel, locale, operating-hour state, current contactability,
inbound purpose, and opt-out state. The first matching rule is selected. No match returns the
policy-owned `noMatchDisposition`.

Templates use `mustache_keys_v1`: placeholders are exact `{{key}}` tokens where `key` matches
`[A-Za-z0-9_]+`. The template's bytes must match `templateDigest`; every placeholder must be declared
in `allowedSubstitutionKeys` and supplied exactly once by the request, and undeclared substitutions
are rejected. The rendered UTF-8 bytes determine `normalizedPayloadDigest`. Template content outside
declared substitutions is never generated or rewritten.

The decision carries caller-supplied decision and idempotency identities, the exact current
`ExternalMessageIdentity` and capture timestamp, exact policy/lexicon versions, selected rule and
template bindings, sender, recipient, purpose, payload digest, source
evidence, and expiry calculated from the rule's owner-supplied seconds. It is a draft boundary only;
sending still requires Habitat and connector permit redemption.

## Atomicity and outcomes

The decision, and a matched opt-out's canonical Suppression, commit in one PostgreSQL transaction.
Failure to persist either rolls both back. Suppression is visible before any later acknowledgment
effect is admitted. `AcknowledgmentOutcome.captureEventId` equals the decision's external-message
identity record ID, and `captureCommittedAt` equals its `capturedAt`; that identity must be the exact
current `ExternalMessageIdentity` at decision admission. The outcome binds these values to the exact
decision and terminal or reconciliation state. Accepted, delivered, failed, and unknown outcomes
remain provider/evidence attributable. Capture and outcome timestamps provide the typed event
boundary for `acknowledgment_latency_seconds`; the SLO objective remains governed by the telemetry
catalog rather than this implementation.

## Compatibility

Version 1.0 attribution and consent-presentation records remain audit-readable. Writers emit 1.1.0.
Version 1.0 has no executable acknowledgment or opt-out records and cannot activate PKT-05.
