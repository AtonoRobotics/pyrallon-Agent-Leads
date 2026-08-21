# Production Implementation Remediation Plan

Status: active; release remains prohibited until every production gate has independently reconstructable evidence.

## Authority and specification closure

- Preserve the existing governing product and runtime contracts.
- Add the Revision 2 closure package as an additive, explicitly linked authority layer.
- Validate every closure schema and fixture against JSON Schema in CI.
- Keep the production completion ledger, execution mandate, execution state, and `GATES.md` versioned with the repository.

## Runtime correction

- Habitat owns observation, work selection, context compilation, cognitive invocation, proposal evaluation, semantic progression, replanning, run lifecycle, continuation, sleep, and effect admission.
- Temporal is a replaceable durability adapter for signals, timers, retries, replay, cancellation, recovery, and activity execution.
- PostgreSQL remains canonical business truth; provider receipts remain provider truth.
- Every provider-changing operation re-enters Habitat and receives a single-use permit immediately before dispatch.

## Verification and release

- CI runs whole-repository scope, anti-drift, ledger, cursor, closure, schema, migration, PostgreSQL, Temporal, iOS, backup, evaluation, lint, type, build, and secret checks.
- Provider-backed E2E, backup/restore, iOS, and release-signature evidence remain explicit gates.
- No gate or ledger item may be marked complete from static presence, mocked provider responses, skipped tests, or agent-authored summaries.
