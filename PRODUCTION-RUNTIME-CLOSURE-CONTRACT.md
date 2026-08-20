# Production Runtime Closure Contract

Revision: 1.0.0
Authority: Revision 7.1 product requirements, ontology 0.3.0, and the 15-family contract package
Purpose: Close production-runtime semantics previously left as implementation gaps. This document is normative and is consumed by the existing contract families; it does not create an ungoverned contract family.

## 1. Authority and configuration boundary

The platform defines behavior. Deployment configuration supplies values.

Platform-defined behavior includes state derivation, ordering, idempotency, ETags, command atomicity, provider-outcome handling, evidence binding, expiry, and fail-closed behavior.

Configuration supplies broker policy, providers, service zones, capacity, hours, templates, corpus, retention, and route identities. Configuration is versioned, tenant-scoped, effective-dated, validated, and auditable. Missing required configuration produces configuration_incomplete; it never causes an implicit default or a new interpretation.

The initial deployment profile is Texas residential buyer representation for an independent agent, covering San Antonio, Fredericksburg/Hill Country, and Austin. Hours, channels, providers, zones, templates, capacity, and routes remain adjustable settings.

## 2. JourneyState compiler

JourneyState is a deterministic projection, never canonical truth.

Input is a tenant/journey-scoped snapshot of current canonical records at one canonical version. The compiler:

1. rejects mixed-tenant or mixed-version input;
2. selects only records whose status is current and whose effective interval contains the observation time;
3. applies the state-machine precedence below;
4. emits every orthogonal state plus blocker codes and source references;
5. emits unknown when required source data or configuration is absent;
6. includes the compiler version, input digest, output digest, and evidence references.

Precedence is explicit and stable:

- ingress: rejected > identity_ambiguous > identified > captured;
- contactability: suppressed > invalid > temporarily_unavailable > contactable > unknown;
- acknowledgment: unknown_outcome > failed > pending > delivered > sent > not_required;
- qualification: contradicted > stale > declined > sufficient_for_consult > collecting > not_started;
- consultation: blocked > provider_pending > booked > completed > cancelled/no_show > ready > not_ready;
- nurture: paused > active > dormant > completed > inactive;
- representation: revoked > represented > pending > not_represented.

Equal-precedence conflicts produce a blocker and unknown; the compiler never chooses by database order.

## 3. Operator projection and commands

JourneyView is the canonical read model. Its ETag is:

sha256 plus SHA-256 of RFC-8785-JCS over tenant, principal, journey, canonical_version, compiler_version, and view_payload.

The same input vector always produces the same view and ETag. Views never include provider credentials or ungrounded cognitive claims.

All operator commands include actor authorization, tenant/journey scope, target record/version, idempotency key, payload digest, issued/expiry times, and expected canonical version.

### Approval commands

approve and deny create an immutable successor Approval record. They never mutate the prior record in place.

- The successor references the prior approval with supersedes.
- The successor contains the decision, deciding actor, decision time, reason, evidence, and exact payload digest.
- The prior record becomes superseded in the append-only version chain.
- A command cannot decide an already decided approval unless it is an explicit revoke_approval command.
- Prohibited actions have no approvable transition.

### Workflow commands

pause_workflow, resume_workflow, and request_reconciliation use a transactional outbox:

1. validate authorization and expected versions;
2. persist the idempotent Command and pending CommandResult;
3. persist the WorkflowReference version;
4. commit;
5. dispatch the Temporal signal from the outbox;
6. persist the signal receipt or typed dispatch failure.

A retry with the same idempotency key returns the original CommandResult. Temporal never becomes canonical truth.

## 4. Capability and connector activation

A provider-changing call is eligible only when all references agree:

- current ReleaseActivation;
- current signed CapabilityInventory;
- exact capability/action-class mapping;
- connector grant and delegated principal;
- current EffectDraftPreview;
- current Habitat EffectPermit;
- matching payload, target, recipient, idempotency, and execution window.

The capability identifier is the exact capabilityId declared in ReleaseActivation and CapabilityInventory. The system never derives it from a connector name, substring, or adapter convention. Any mismatch is activation_binding_mismatch.

## 5. Telemetry binding

Every metric definition declares:

- metric identity and unit;
- start and end event types;
- correlation key;
- numerator and denominator event-set digests;
- dimensions and window;
- threshold values;
- owner and retention class.

Every observation repeats the metric-definition reference, event types, event-set digests, correlation digest, dimensions, numerator, denominator, window, and calculation state. A ratio with no denominator is unknown or not_applicable according to the metric definition; it is never silently zero.

## 6. Accessibility binding

Web and iOS acceptance is represented by AccessibilityAcceptance. Production closure evidence is AccessibilityEvidence.

An activation may use accessibility evidence only when an AccessibilityBinding references:

- the exact operator acceptance record;
- the exact closure evidence record;
- the exact surface;
- the exact build digest;
- the binding digest;
- a non-expired evidence interval.

A missing or expired binding blocks that surface. Waivers require explicit scope, compensating control, approver authority, and expiry.

## 7. Temporal production worker

Worker behavior is fully configuration-driven but structurally fixed.

A WorkerConfiguration must declare task queue, workflow types, activity types, concurrency limits, cache size, graceful shutdown, deployment digest, and effective interval. The worker rejects an incomplete configuration.

The production worker inventory is:

- BuyerJourneyWorkflow;
- QualificationChildWorkflow;
- NurtureChildWorkflow;
- ConsultationChildWorkflow;
- ConnectorReconciliationWorkflow.

Temporal owns timers, signals, retries, leases, recovery, compensation, and lifecycle. PostgreSQL owns business truth. Habitat is re-entered before every external effect.

## 8. Qualification and booking

Qualification readiness is computed from the current versioned policy, criterion observations, freshness, service-zone reference, capacity reference, escalation policy, and consent/contactability state. Missing policy or configuration yields configuration_incomplete; it does not make a buyer ready.

Booking uses a provider snapshot and watermark. Slot identity is deterministic over provider, calendar, snapshot watermark, start, end, timezone, location, and policy version. A slot expires at the earliest of its configured expiry, provider snapshot expiry, calendar-version change, policy-version change, or currentness failure.

Booking, reschedule, and cancel are provider commands with receipts. Unknown provider outcomes enter reconciliation and cannot be retried as a new command until reconciled.

## 9. Release acceptance

The full product is admitted only when every requirement has implementation, integration/fault tests, operational evidence, and a signed release record bound to:

- source/build digest;
- 15-family manifest digest;
- migration head;
- worker configuration;
- connector capability inventory;
- route and corpus configuration;
- accessibility binding;
- telemetry catalog;
- gate registry and evidence bundle.

No capability is marked complete by a prose claim.