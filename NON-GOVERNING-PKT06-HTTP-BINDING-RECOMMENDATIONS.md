# Non-Governing PKT-06 HTTP Binding Recommendations

Status: implementation audit only; requires specification-owner publication.

This document does not amend any contract, schema, ledger, packet status, gate, or activation
decision. It records an HTTP binding gap found while reconciling the PKT-06 control-plane facade
with the published connector and Habitat contracts.

## Observed contract boundary

The provider-neutral connector kernel accepts four separately governed inputs:

- a `ConnectorRequest` and its exact normalized payload bytes;
- the matching Habitat registration and redeemed permit for effect-bearing capabilities;
- the current signed capability inventory and exact `EffectDraftPreview`; and
- deployment-supplied grant, activation, credential-isolated adapter, and inventory authorities.

It validates payload digest, current grant and delegated principal, capability/action/constraint
mapping, preview identity and execution window, release activation, and exact permit bindings before
calling an adapter.

The current `POST /v1/connectors/invoke` facade supplies only the JSON `ConnectorRequest` and a
permit-digest header to a separate fail-closed service. No published HTTP contract defines how the
normalized payload bytes, `EffectDraftPreview`, Habitat registration, current inventory, activation
authority, or credential-isolated adapter are resolved and bound atomically. The governing audit
also leaves the Habitat effect-context and connector-to-release-activation identity mappings open.

The facade therefore cannot safely delegate to the connector kernel. It now reports absent live
adapter configuration as `configuration_incomplete`; it does not classify that condition as a
revoked connector grant or invoke a provider.

## Required governing publication

Before the connector invocation HTTP route can activate, the governing owner should publish:

- a versioned request binding for exact payload bytes or a governed artifact reference and digest;
- exact references to the `EffectDraftPreview`, Habitat decision/registration, and redeemed permit;
- the server-side lookup and currentness rules for signed capability inventory and connector grant;
- the governed connector/capability-to-release-activation `capabilityId` mapping;
- the deployment adapter identity and credential-broker binding without exposing credential material;
- transaction and ordering rules between permit redemption, provider invocation, receipt storage,
  response admission, and unknown-outcome reconciliation;
- replay, retry, timeout, conditional-version, revocation-race, and webhook correlation behavior;
- typed HTTP outcomes and valid/invalid fixtures for every binding and failure mode.

## Current safe implementation state

- The provider-neutral connector kernel and its deterministic validation remain available.
- No live provider adapter is selected or invoked by the control-plane facade.
- Missing adapter/runtime wiring returns `configuration_incomplete` rather than falsely reporting
  connector revocation.
- The HTTP route must not be represented as a complete PKT-06 effect boundary until the published
  bindings and deployment runtime exist.

Passing kernel or facade tests must not be represented as live connector evidence, PKT-06
completion, or production activation.
