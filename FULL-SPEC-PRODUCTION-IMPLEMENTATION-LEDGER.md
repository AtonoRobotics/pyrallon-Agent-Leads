# Full-Spec Production Implementation Ledger

**Baseline:** Revision 7.1, 15-family authority package, commit `90639a5`
**Target:** Complete production implementation; no reduced release, capability omission, or “follow-on” architecture.
**Branch:** `build/full-spec-production`

## Non-negotiable completion rule

The system is complete only when every requirement in the PRD, gateway contract, ontology, operational thread, gate registry, and completion ledger has:

1. a versioned executable contract;
2. production implementation;
3. integration and fault/replay tests;
4. operator-facing behavior where applicable;
5. observability and alert evidence;
6. migration, recovery, and rollback behavior;
7. deployment configuration and provider readback;
8. signed gate evidence bound to the exact build and contract manifest.

Passing unit tests or validating the kernel does not close a requirement.

## Full implementation surface

| Area | Required production result | Required completion evidence |
|---|---|---|
| Canonical CRM and ontology | All ontology 0.3.0 records, orthogonal journey state machines, epistemic transitions, identity resolution, correction, supersession, contradiction, tenant isolation | PostgreSQL reconstruction, concurrency, RLS, migration/repair, adversarial transition suites |
| Evidence and projections | Immutable evidence, artifact retention/legal hold/deletion, hash verification, rebuildable pgvector/Neo4j projections, deletion/rebuild fencing | Tamper, retention, deletion, concurrent rebuild, stale-epoch, recovery evidence |
| Habitat authority | Event admission, current policy/authority, consent, agreement qualification, idempotency, just-in-time permits, per-effect re-entry, replay/revocation races | DW2-C1 suites, permit receipts, authority-decision evidence, provider-effect denial tests |
| Temporal execution | JourneyState compiler, parent/child workflows, signals, timers, leases, retries, compensation, recovery, worker lifecycle, upgrades, replay | Production worker deployment, crash/replay/upgrade tests, unknown-outcome reconciliation, runbook |
| Ingress and acknowledgment | Web/form, email, SMS, signature validation, stable external identity, identity resolution, opt-out, deterministic acknowledgment, SLO telemetry | Live sandbox receipts, two-minute SLO, duplicate/replay/conflict, suppression-first evidence |
| Connector gateway | Email, SMS, calendar, document/signature connectors, credential isolation, capability inventory, preview/grant/permit binding, receipts and reconciliation | Provider capability readback, webhook tests, revocation, unknown outcome, live sandbox evidence |
| Cognitive runtime | Context compiler, ontology/knowledge grounding, Codex subscription and API routes, fixed route policy, proposal-only output, degradation, capacity/auth handling | Route qualification, grounded proposal evals, subscription/API transition traces, fail-closed degradation |
| Qualification | Progressive questions, readiness predicate, service zones, capacity, escalation, fairness allowlists, parity/counterfactual gates | Deterministic decision fixtures, fair-housing evals, policy promotion/rollback evidence |
| Consultation booking | Availability snapshots, SlotSet, expiry/currentness, booking/reschedule/cancel, provider truth, reminders, reconciliation, agent briefing | Calendar sandbox/live evidence, stale-slot/conflict/unknown-outcome tests, receipts |
| Operator web surface | JourneyView projection, exceptions, approvals, deny/approve semantics, pause/resume/reconcile, ETags, idempotency, authorization, version conflicts | API contract tests, browser acceptance, WCAG 2.2 AA evidence, concurrency/replay tests |
| iOS surface | Offline read/cache, queued commands, reconnect revalidation, conflict handling, accessibility, signed build binding | Device/integration tests, offline/reconnect evidence, WCAG/accessibility evidence |
| Knowledge and memory | Ontology-aware knowledgebase, source freshness, semantic retrieval, graph projection, provenance, supersession, compaction/reconstruction | Retrieval grounding, stale-source, deletion propagation, long-horizon context evals |
| Observability | Metric identities, event bindings, ratios, dimensions, windows, thresholds, SLOs, dashboards, alerts, ownership, retention | Catalog validation, emitted-event evidence, dashboard/alert fire tests, retention proof |
| Activation and operations | ReleaseActivation, authorization lineage, capability inventory, signed evidence, deployment checks, rollback, incident/recovery runbooks | Signed activation readback, exact-build binding, rollback drill, production qualification |

## Required owner inputs

OPEN-001–009 are typed configuration records, not permanent blockers. They must be entered, validated, versioned, and bound to the release:

- sponsoring broker and automation policy;
- cognitive route matrix and entitled subscription identities;
- enabled mail/calendar/document providers;
- acquisition and publishing authority;
- approved Texas forms/templates and signature flow;
- service-zone polygons, consultation locations, travel/capacity policy;
- baseline funnel metrics;
- approved knowledge corpus and effective-date ownership;
- retention, legal hold, deletion SLO, and object-lock policy.

## Activation rule

`activatable: true` is permitted only when every row above has implementation and evidence, every applicable gate passes, all owner inputs are current, and the signed activation record references the exact build, contract manifest, migrations, provider capability inventory, and evidence bundle.

No implementation agent may mark a row complete from a narrative summary.