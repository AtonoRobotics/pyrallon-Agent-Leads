# Closure verification matrix

| ID | Requirement | Owner/provider | Derivation/schema | Fixture | Test/evidence | Status |
|---|---|---|---|---|---|---|
| C-001 | source precedence and JourneyView | Projection service | operator-projection.schema.json | operator-projection.valid.json | projection determinism + ETag | closed |
| C-002 | workflow command lifecycle | Command service/Temporal | workflow-command.schema.json | workflow-command.valid.json | idempotency, lease, signal recovery | closed |
| C-003 | approval CAS transition | Policy gateway | approval-transition.schema.json | approval-transition.valid.json | transition/property tests | closed |
| C-004 | canonical-to-Temporal compiler | Journey compiler | journey-state-compiler.schema.json | journey-state.valid.json | rebuild byte equality | closed |
| C-005 | telemetry start/end and ratios | Telemetry service | telemetry-binding.schema.json | telemetry-binding.valid.json | catalog binding + SLO replay | closed |
| C-006 | accessibility release binding | Release authority | accessibility-evidence.schema.json | accessibility-evidence.valid.json | WCAG evidence gate | closed |
| C-007 | Habitat effect context | Habitat/policy gateway | effect-context.schema.json | effect-context.valid.json | stale digest denial + permit | closed |
| C-008 | connector capability activation | Connector registry | connector-capability-binding.schema.json | connector-binding.valid.json | one-to-one mapping | closed |
| C-009 | live activation evidence | Release authority/provider | activation-evidence.schema.json | activation-evidence.valid.json | signed live-verb chain | closed |
| C-010 | reference domains and supersession | Canonical store | reference-domain.schema.json | reference-domain.valid.json | cardinality/tenant/time tests | closed |
| C-011 | representation concurrency | Canonical store | representation-concurrency.schema.json | representation-conflict.valid.json | CAS conflict test | closed |
| C-012 | reconciled completion evidence | Evidence service | reconciliation-evidence.schema.json | reconciliation-evidence.valid.json | linkage and hash test | closed |
