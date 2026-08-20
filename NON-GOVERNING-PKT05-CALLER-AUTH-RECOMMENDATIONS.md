# Non-Governing PKT-05 Caller Authentication Recommendations

Status: implementation audit only; requires specification-owner publication.

This document does not amend any contract, schema, ledger, packet status, gate, or activation
decision. It records a caller-authentication gap found while reconciling the PKT-05 control-plane
routes against the published authority.

## Observed contract boundary

The implementation exposes two distinct ingress routes:

- `IngressService.admit_envelope` accepts the provider-neutral inbound envelope only when its caller
  supplies an authenticator, payload-artifact verifier, and capture handler. The current control
  plane does not yet provide a governed deployment wiring seam for that runtime bundle, so
  `POST /v1/ingress/envelope` remains unconditionally unavailable. This fail-closed library boundary
  matches the published provider requirements; the HTTP wiring remains unfinished.
- `POST /v1/ingress` directly admits normalized `AttributionInput` and
  `ConsentPresentationEvidence` records into tenant-scoped storage.

The published contracts do not define the caller class or authentication mechanism for the second
route. `ActorTenantAuthorization` governs operator surfaces and commands, but no publication assigns
the raw ingress route an operator command or record scope. The same records may also originate from
provider, connector, ingress, configuration, or service-principal paths. Requiring a human/operator
actor would therefore invent an operator-only caller model; accepting only a global control token
and caller-selected tenant does not independently prove tenant authority.

## Required governing publication

Before the raw normalized-ingress HTTP route can activate, the governing owner should publish:

- the permitted caller types for each admitted message type;
- the credential, signature, or current canonical authorization record that authenticates each
  caller type;
- server-side tenant derivation and its binding to the record's `tenantId`;
- permitted record scopes, source identities, and attribution/evidence requirements;
- replay, revocation, expiry, and credential-rotation behavior;
- typed outcomes for absent, invalid, expired, revoked, cross-tenant, or scope-incompatible callers;
- atomicity requirements between authentication, record admission, and evidence persistence;
- valid and invalid fixtures plus tests for operator, service-principal, connector, and provider
  cases that the owner elects to admit.

## Current safe implementation state

- Provider-envelope admission fails closed without explicitly injected provider configuration, and
  the current HTTP route remains unavailable until governed deployment wiring supplies it.
- Provider-neutral structural validation, authentication interfaces, artifact verification, and
  durable replay detection remain implemented.
- The raw normalized-ingress service remains useful as an internal library seam, but its generic
  HTTP route must not be represented as production-authenticated or activated.
- No operator-only, service-principal, connector, or provider caller model has been selected by the
  implementation.

Passing storage or unit tests must not be represented as proof that the raw HTTP caller is authorized
for a tenant or that PKT-05 is complete.
