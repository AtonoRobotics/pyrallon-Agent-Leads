# Non-Governing PKT-09 Reference Recommendations

Status: implementation audit only; requires specification-owner publication.

This document does not amend any contract, schema, ledger, packet status, gate, or activation
decision. It records executable-reference gaps found while implementing PKT-09 against the
published 15-family authority incorporated on this branch.

## Observed contract boundary

`AVAILABILITY-BOOKING.schema.json` requires every `AvailabilityPolicy` to carry exact references
to `TravelPolicy`, `LocationPolicy`, `ServiceZonePolicy`, and `CapacityPolicy`. The published valid
fixture uses those four record-type names. None is defined in the ontology, another contract-family
schema, or the generated models.

The contract requires `availability_v1` to apply travel, location, service-zone, and capacity
exclusions while deriving a `SlotSet`. No published binding defines the referenced policies'
fields, lifecycle, effective interval, supersession behavior, exclusion decisions, precedence, or
how their versions contribute to the SlotSet input digest or any governed invalidation watermark.
A runtime can therefore validate a caller-supplied `SlotSet`, but cannot independently derive its
governed slot contents from the published records.

The base slot-enumeration text also leaves several choices non-executable. It does not identify the
origin of the `slotIncrementSeconds` grid; state whether a candidate end or its buffered interval
must remain within a weekly window, search horizon, or snapshot range; bind `Slot.timeZone` to the
policy timezone; or define whether before/after buffers apply to busy intervals, blackouts,
candidate-to-candidate separation, or some combination. The implementation therefore enforces the
published consultation duration but does not choose those remaining predicates.

`BookingCommand.actorRef` is a generic record reference whose valid fixture names `Actor`, another
type absent from the governing schemas and generated models. The publication does not provide an
exact governed replacement or define how that actor reference binds to the separately required
authorization and effect-intent references.

`BookingReconciliation` also requires a `providerObservationRef`. The valid fixture identifies its
target as `ProviderObservation`, but that record type is not defined in a governing schema or
generated model. The publication therefore does not define the observation identity, provider and
tenant binding, version, event time, evidence, lifecycle, or the exact predicate connecting an
observation to a reconciled booking outcome and canonical `Appointment`.

`BookingResult.providerReceiptRef` is also a generic record reference. The successful-result path
requires a provider receipt for reconstruction, but no permitted receipt record type or exact
mapping to an already-governed receipt type is published. A null reference in the valid fixture
does not define the production success path.

The existing `contract_acceptance.py` functions correctly validate deterministic ordering, expiry,
exact input references, readiness, binding state, provider watermark, authority context,
idempotency, and reconciliation consistency from supplied records. They do not generate the four
policy decisions, prove those policy references current, invoke a calendar provider, or establish
that a referenced provider observation exists and supports the asserted outcome.

## Required governing publication

Before production SlotSet derivation or booking reconciliation can activate, the governing owner
should publish one of the following equivalent closures:

1. Versioned schemas and generated models for `TravelPolicy`, `LocationPolicy`,
   `ServiceZonePolicy`, `CapacityPolicy`, `ProviderObservation`, `Actor`, and the permitted provider
   receipt record types; or
2. Exact references to already-governed record types that replace those names.

The publication also needs:

- stable identities, tenant ownership, versions, lifecycle, effective intervals, and supersession;
- deterministic travel, location, service-zone, and capacity exclusion inputs and predicates;
- the slot-increment origin and exact boundary and inclusivity/exclusivity rules for windows,
  horizon, snapshot range, blackouts, busy intervals, and buffers;
- the required relationship, if any, between a slot timezone and its policy timezone;
- precedence and conflict behavior when multiple exclusions apply;
- exact current-record and SlotSet input-digest construction rules, provider-watermark invalidation,
  and an explicit decision about whether and how policy-version changes bind to any other governed
  watermark;
- provider-observation ownership, binding, event-time, evidence, and receipt relationships;
- the exact observation-to-`BookingCommand`, `BookingResult`, `BookingReconciliation`, and
  `Appointment` relationship cardinalities and validation behavior;
- typed failure outcomes for missing, stale, superseded, cross-tenant, contradictory, or mismatched
  references and unknown provider outcomes;
- valid and invalid fixtures, generated-model updates, migration compatibility, and deterministic
  cross-record tests, including DST boundaries and provider reconciliation races.

## Current safe implementation state

- Structural and deterministic semantic acceptance remains available.
- Exact-version, tenant-scoped readers remain available.
- Durable family storage remains inactive unless reached through a separately admitted writer.
- No production SlotSet derivation, booking writer, provider effect, reconciliation writer, or
  canonical Appointment mutation is activated.
- Table presence and caller-supplied fixtures are not treated as proof that cross-record references
  were admitted or current.

Passing fixture tests must not be represented as proof of governed slot derivation, provider truth,
PKT-09 completion, or production activation.
