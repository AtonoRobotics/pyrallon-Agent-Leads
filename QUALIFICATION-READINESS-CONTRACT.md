# Qualification and Consultation Readiness Contract 1.0

`QUALIFICATION-READINESS.schema.json` is authoritative for policy ownership, progressive question selection, and deterministic consultation-readiness derivation.

An active `QualificationPolicy` is supplied by its declared brokerage, license-holder, or deployment-operator owner. It contains the complete criterion set, dispositions, accepted observation states, freshness, question templates, missing/contradiction behavior, exact tie-break rule, and exact references to service-zone, capacity, and urgent-escalation policy. The runtime supplies no default criterion, threshold, owner, template, zone, capacity, or escalation behavior.

The context compiler constructs `QualificationInputSet` from exact current canonical versions. Inferred observations never satisfy a criterion unless a later contract version explicitly permits that state. Stale, contradicted, missing, or cross-tenant records fail according to the active policy; absence of any required policy/reference returns `configuration_incomplete`.

`NextQuestionDecision` uses `priority_then_criterion_id_ascending`. It selects only an unresolved criterion whose policy disposition requires asking or agent handling. It does not create a canonical fact or authorize communication.

`ReadinessDecision` uses `all_required_resolved_no_blocking_contradiction_zone_and_capacity_v1`: every required criterion must have one current accepted observation, every declinable criterion must have an accepted observation or `buyer_declined`, no policy-blocking contradiction or urgent escalation may remain, and current service-zone and capacity decisions must both pass. Decisions bind the exact policy, input digest, canonical watermark, deriver implementation, evidence, and expiry. They are derived records, not authority.

Policy versions are append-only. Version 1 has no predecessor; each later version increments by one and names the same stable policy identity through `supersedesRecordId`. Only active policies apply. Effective intervals are half-open. Superseded and retired policies remain audit-readable.

Acceptance requires schema fixtures, generated models, deterministic selection/readiness tests, cross-tenant/reference/freshness failures, contradiction and decline cases, reproducible input digests, policy supersession, and counterfactual/parity evaluation under the existing fair-housing contract.
