# Operator Surface Contract 1.1

`OPERATOR-SURFACE.schema.json` is authoritative for web and iOS projections and commands. A
`JourneyView` is a versioned projection, never canonical state: clients must display its generation
time, source-backed epistemic state, blockers, recovery owner, and canonical version. They must not
collapse journey, contactability, acknowledgment, qualification, consultation, nurture, or
representation into one state.

Every mutation is an `OperatorCommand`. The server authenticates the actor, re-reads every listed
Authorization and the policy version, verifies tenant and target type, compares `expected_version`,
and verifies `payload_digest` before any state change. Approval does not grant authority. Correction
creates a replacement or invalidation record; revocation is append-only; pause/resume applies only
to the currently bound `WorkflowReference`. A reused idempotency key returns `duplicate` only for
the same payload digest and otherwise returns `payload_mismatch`.

Offline clients may queue commands but cannot claim success. On reconnect, every queued command is
re-authenticated and revalidated against current authority, policy, target version, grant state, and
workflow ownership. Stale commands return a typed conflict or denial and expose current version;
they are never silently rebased. Errors expose safe details only and remain correlated to immutable
decision evidence.

Exception presentation must name the blocker category, evidence, recovery owner, and earliest known
recovery time. Evidence summaries preserve epistemic labels and source identity; inferred or stale
content cannot be presented as fact. Web and iOS builds require an `AccessibilityAcceptance` record
for WCAG 2.2 AA, automated and manual suites, at least one relevant assistive technology, zero
blocking violations, immutable artifact digest, evaluator identity, and build version. Missing or
expired acceptance blocks release activation.

## Canonical mutation payloads

Operator Surface 1.1 closes OPEN-028. A command never stands in for an ontology record or its
evidence. Every canonical mutation carries the complete proposed ontology record or records; each is
validated under ontology `buyer-ops/0.3.0`, current same-tenant state, and the repository transaction
before any write.

- `correct_replace` carries an applied `Correction`, the exact next version of the corrected
  epistemic item, and the complete new replacement item.
- `correct_invalidate` carries an applied `Correction` and the exact invalid next version of the
  corrected epistemic item. It forbids a replacement item.
- `revoke_authorization` carries the complete next `Authorization` version, including
  `revocationEvidenceId` and `revokedAt`.
- `revoke_approval` carries the exact closing version of the prior approval plus a new append-only
  revoked `Approval` whose `supersedesId` identifies it. It does not overwrite the prior decision.
- `approve` and `deny` carry the exact closing version of a pending approval plus a new append-only
  decided `Approval` whose `supersedesId` identifies it. A decided approval cannot be decided again.

The command does not generate canonical identifiers, attribution, evidence, effective times,
replacement content, or revocation reasons. Those values are supplied by the authorized caller and
validated. `payload_digest` binds the command type, target identity and expected version, reason, and
the complete canonical mutation using RFC 8785 canonical JSON and SHA-256.

## Authority and policy revalidation

`AuthorityBinding` carries versioned references rather than unversioned IDs. Before mutation the
server re-reads each authorization reference and the policy reference, and requires exact tenant,
record type, version, current status, actor/grantee, action class, resource type, resource ID,
effective interval, and command-target agreement. An approval cannot widen those bindings.

The canonical writes, immutable decision evidence, and `CommandResult` idempotency record commit in
one database transaction. Failure of any record validation or result persistence rolls back every
write. Reconnect uses the same transaction and does not silently update expected versions.

## Compatibility

Operator Surface 1.1 readers accept only `operator-surface/1.1.0`. Version 1.0 commands are retained
for audit but are not executable because their mutation and authority bindings are incomplete. Views,
results, errors, and accessibility records are mechanically version-bumped without changing their
1.0 field meanings.
