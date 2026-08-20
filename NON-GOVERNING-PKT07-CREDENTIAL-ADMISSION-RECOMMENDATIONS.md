# Non-Governing PKT-07 Credential Admission Recommendations

Status: recommendation only. This document is not a contract, policy, activation decision, or authority grant.

## Observed governing gap

The Cognitive Runtime Gateway contract defines the shape and eligibility use of a `CredentialIdentity`, and requires PostgreSQL to own credential references and routing configuration. The published package does not define an executable command or admission boundary that answers all of the following:

- which principal may create, replace, revoke, or supersede a credential identity;
- how an exact current identity is loaded and bound to its tenant, subject, provider, authentication class, billing class, allowed action classes, allowed model families, concurrency limit, data-policy version, state, and expiry;
- which adapter selections determine `providerId`, `authClass`, `billingClass`, and `subjectType`, versus which values require owner policy;
- which expected version, idempotency key, supersession evidence, or conflict outcome governs replacement;
- how credential material is attached without allowing a caller to overwrite policy-bearing identity fields;
- how one-time OAuth sessions are claimed, exchanged, persisted, consumed, retried, and reconciled atomically;
- what happens when a provider omits, zeroes, or changes credential expiry;
- which operator command, authorization scope, and release evidence may invoke each credential-admission path.

Structural validation of a caller-submitted `CredentialIdentity` does not establish any of those authorities.

## Required owner publication

The governing owner should publish either:

1. a versioned credential-identity admission and material-binding contract, including commands, authorization, concurrency, supersession, lifecycle, typed failures, atomic OAuth behavior, fixtures, and compatibility rules; or
2. an exact reference-loading contract for identities admitted elsewhere, plus a separate material-binding contract that cannot mutate the referenced identity.

The publication should also bind adapter facts to provider/authentication/billing/subject classifications and specify provider-expiry handling. These are owner decisions; this report does not choose them.

## Safe implementation state

Until that publication is available:

- the control-plane OAuth start/poll, metered credential, and local-runtime binding routes return `configuration_incomplete`;
- those paths do not contact a provider, create an OAuth session, validate, use, persist, bind, or admit supplied credential material, create a `CredentialIdentity`, overwrite an existing identity, or activate a runtime;
- read-only listing of previously stored credential references remains available behind the existing authenticated tenant boundary;
- simulated gateway and routing tests are implementation evidence only and are not live-provider or production-activation evidence;
- PKT-07 remains incomplete and production activation remains false.
