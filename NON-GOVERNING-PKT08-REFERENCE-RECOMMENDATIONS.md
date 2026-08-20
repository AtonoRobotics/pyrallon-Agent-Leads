# Non-Governing PKT-08 Reference Recommendations

Status: implementation audit only; requires specification-owner publication.

This document does not amend any contract, schema, ledger, packet status, gate, or activation
decision. It records an executable-reference gap found while implementing PKT-08 against the
published 15-family authority at commit `1cfd6a0`.

## Observed contract boundary

`QUALIFICATION-READINESS.schema.json` requires every `QualificationInputSet` to carry:

- `serviceZoneDecisionRef` plus `serviceZoneEligible`;
- `capacityDecisionRef` plus `capacityAvailable`;
- zero or more `urgentEscalationRefs`; and
- a `canonicalWatermark`.

Every `QualificationPolicy` also requires exact service-zone, capacity, and urgent-escalation policy
references, while each criterion requires a question-template reference.

The published valid fixture identifies the first two references as `ServiceZoneDecision` and
`CapacityDecision`. It identifies the policy and template targets as `ServiceZonePolicy`,
`CapacityPolicy`, `UrgentEscalationPolicy`, and `QuestionTemplate`. None of these six record types is
defined in the ontology, another contract-family schema, or the generated models. No published
binding defines how the boolean fields are derived from the decision records, how the policies bind
those decisions, how a question template resolves to governed content, or how the watermark proves
their currentness.

The existing `contract_acceptance.py` validator correctly validates deterministic qualification
selection, freshness, contradictions, input digest, and readiness results from a supplied input set.
It cannot establish that the referenced service-zone, capacity, or escalation decisions exist,
belong to the tenant, have the named versions, are current and effective, or support the copied
boolean values.

## Required governing publication

Before a production `QualificationInputSet` or derived-decision writer can activate, the governing
owner should publish one of the following equivalent closures:

1. Versioned schemas and generated models for `ServiceZoneDecision`, `CapacityDecision`, and the
   permitted urgent-escalation record types, plus `ServiceZonePolicy`, `CapacityPolicy`,
   `UrgentEscalationPolicy`, and `QuestionTemplate`; or
2. Exact references to already-governed record types that replace those names.

The publication also needs:

- stable identities, tenant ownership, versions, lifecycle, effective intervals, and supersession;
- the exact fields that bind service-zone eligibility and capacity availability;
- the bindings from each qualification policy reference to the applicable decision and the exact
  question-template content/version;
- allowed urgent-escalation record types and the blocking predicate;
- canonical-watermark construction and comparison rules;
- current-record and cross-tenant validation behavior;
- typed failure outcomes for missing, stale, superseded, contradictory, or mismatched references;
- valid and invalid fixtures, generated-model updates, migration compatibility, and deterministic
  cross-record tests.

## Current safe implementation state

- Structural and deterministic semantic acceptance remains available.
- Exact-version, tenant-scoped readers remain available.
- Durable family storage remains inactive unless reached through a separately admitted writer.
- No production `QualificationInputSet` writer or qualification-decision writer is activated.
- Table presence alone is not treated as proof that cross-record references were admitted.

Passing tests over caller-supplied fixtures must not be represented as proof of current canonical
reference validity, PKT-08 completion, or production activation.
