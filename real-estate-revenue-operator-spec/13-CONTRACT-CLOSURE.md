# Contract Closure — Revision 2.0

This document closes the previously implicit semantics. It is normative and has precedence over any earlier statement that calls these items unresolved. A design requirement is **closed** when its owner, provider, derivation, schema, errors, compatibility rule, test and release evidence are named in `contracts/closure/VERIFICATION-MATRIX.md`.

## 1. Authority and source precedence

For a tenant and snapshot watermark, truth is selected in this order: (1) committed canonical PostgreSQL records; (2) accepted, hash-linked domain evidence; (3) Temporal execution state, only for execution status; (4) deterministic operator projections; (5) graph/vector/search projections; (6) agent checkpoints and conversation memory. Lower layers may explain or propose, never overwrite higher layers. Cross-tenant references, stale versions and unsigned evidence are rejected.

## 2. JourneyView and operator projection

`JourneyView` is a deterministic read model identified by `(tenant_id, journey_id, snapshot_watermark, projection_contract_version, redaction_profile)`. The projector reads a repeatable-read canonical snapshot, then accepted evidence ordered by `(effective_at DESC, canonical_version DESC, evidence_id ASC)`. Temporal contributes `execution_status`, `pending_command_ids`, and `last_worker_heartbeat`; it cannot provide person, property, consent, representation, or monetary truth.

The projection emits orthogonal fields: `lifecycle` (`new|active|paused|completed|closed|blocked|uncertain`), `attention` (`none|due|stalled|exception`), `authority` (`authorized|approval_required|denied|expired`), `summary`, `blockers[]`, `next_actions[]`, `source_watermark`, and `etag`. Blockers are sorted by severity (`critical,high,medium,low`) then code. Summary is generated from a fixed template and canonical facts; language models may supply an explanation field only, never a state. ETag is SHA-256 over canonical JSON of the identity tuple and source watermark. Rebuilds are idempotent and must produce the same bytes.

## 3. Workflow commands

`pause_workflow`, `resume_workflow`, and `request_reconciliation` are versioned commands with tenant, actor, journey, expected journey version, idempotency key, reason, and created time. The command service validates authorization and version in one PostgreSQL transaction, appends a command record and outbox row, and returns `accepted|duplicate|rejected`. A Temporal signal carries the command id. The worker persists an outcome before acknowledging the signal; an outbox retry repairs a lost signal. A lease `(workflow_id, owner_id, lease_epoch, expires_at)` prevents concurrent command application. Reconciliation never changes canonical truth without a new authorized command and evidence.

## 4. Approval transition

Approval is a compare-and-set state machine: `pending -> approved|denied|expired|revoked`. Decisions are immutable, carry actor, policy version, reason, timestamp and evidence reference, and are idempotent by `(approval_id, idempotency_key)`. A stale expected version returns `APPROVAL_VERSION_CONFLICT`; an unauthorized or self-approval prohibited by policy returns `AUTHORIZATION_DENIED`. No effect may execute while approval is pending.

## 5. Temporal JourneyState compiler

The compiler consumes canonical snapshot, accepted event cursor, workflow references and policy version and emits a content-addressed `JourneyState` plus compiler version. It is a pure function. Temporal stores the digest and execution metadata; it does not become a source of truth. Event-triggered compilation and full rebuild must be byte-identical for the same inputs. Missing, contradictory or superseded inputs yield explicit `uncertain`/blocker records rather than guessed values.

## 6. Telemetry bindings

Every observation uses catalog ids `journey.command.started`, `journey.command.completed`, `connector.effect.started`, `connector.effect.completed`, `workflow.reconciled`, and `projection.rebuilt`. Start/end pairs share `operation_id`; retries share `attempt_group_id`; each event carries tenant, contract version, actor class, and source evidence. A ratio declares numerator and denominator event ids, identical tenant/cohort/window dimensions, and denominator non-zero policy. An SLO binds to a ratio id and closure family; unbound observations are invalid.

## 7. Accessibility binding

Release activation requires a signed `AccessibilityEvidence` record for web and iOS, WCAG 2.2 AA, with app/build id, test-tool versions, scenario ids, assistive technologies, findings, waivers, evaluator identity and evidence digest. Critical and serious findings must be zero; every waiver needs expiry and owner. Evidence digest and accessibility contract version are part of the activation hash. Any change to UI build, component library, or accessibility policy invalidates prior activation.

## 8. Habitat effect context

Every external effect carries immutable `EffectContext`: tenant, actor, journey, capability id/version, connector binding id/version, capability-inventory version, preview digest, constraint digest, channel, consent/authorization references, policy version, idempotency key, and expiry. Habitat re-reads current bindings and policy, compares all digests, and returns `permit|deny|needs_approval|expired`; mismatch is fail-closed `EFFECT_CONTEXT_STALE`. Preview and execution use the same context digest.

## 9. Connector activation identity

`capabilityId` is canonical `provider_kind:capability_name` (for example `google:gmail.send`). A binding maps exactly one id to adapter id/version, provider, channel, effect class, scopes, tenant, and activation record. Adapters may not invent ids. Activation requires all bindings, scopes, consent, policy and live-verb evidence to match the release hash; changes revoke the activation.

## 10. Live activation evidence

For each enabled capability, the evidence chain is: preflight, consent, policy permit, signed request, provider receipt, reconciliation, canonical mutation and rollback/retry result. Evidence is production-equivalent, tenant-scoped, immutable and linked by operation id. Synthetic provider responses are never accepted. Activation is disabled on policy, adapter, scope, accessibility, contract or evidence-hash change.

## 11. Canonical references and concurrency

Reference domains are closed enums in `reference-domain.schema.json`; every reference is tenant-scoped, type-discriminated, cardinality-validated and temporally valid. `SUPERSEDES` requires identical tenant and semantic subject `(record_type, principal_ids, property_or_transaction_id, jurisdiction)` and a strictly newer version. Representation and sponsorship use CAS on subject scope; competing active records become `representation_conflict` and block effects until an authorized resolution. Silent merges are forbidden. Reconciliation evidence must reference the canonical completion record it proves.

## 12. Completion rule

The specification is complete when the closure verifier passes. Production release is a separate execution state: it requires owner inputs `IN-001..IN-008` and the signed live evidence described above. Those are activation facts, not missing design semantics.

