# Gates: Entire buyer-operations production program

Scope: Complete and release the whole repository in production, including every required specification, workstream, dependency, deployment surface, runtime path, end-to-end proof, backup/restore proof, and evaluation; scope reduction, MVP, slice, prototype, demo, and partial release are prohibited.

- [x] G1: The authoritative production scope remains whole-repository and all twelve required workstreams remain present in the canonical sequence.
  CHECK: uv run python scripts/verify_production_scope.py
  EXPECT: whole-repository production scope is mechanically enforced
  EVIDENCE: whole-repository production scope is mechanically enforced

- [x] G2: The completion ledger contains every required item and is not falsely released before every item has implementation, focused verification, and live end-to-end evidence.
  CHECK: uv run python scripts/verify_production_ledger.py
  EXPECT: production completion ledger is structurally valid
  EVIDENCE: production completion ledger is structurally valid

- [x] G3: The execution cursor advances only in canonical order and has concrete production proof for the current workstream.
  CHECK: uv run python scripts/verify_production_execution.py
  EXPECT: whole-repository production execution cursor is mechanically enforced
  EVIDENCE: whole-repository production execution cursor is mechanically enforced

- [x] G4: The anti-drift guard rejects narrowed, skipped, removed, or generic work and passes for the current full-repository execution state.
  CHECK: uv run python scripts/verify_production_focus.py
  EXPECT: active production workstream focus is mechanically enforced
  EVIDENCE: active production workstream focus is mechanically enforced

- [x] G5: All contract, schema, migration, generated-artifact, security, and repository tests pass together.
  CHECK: uv run pytest -q
  EXPECT: 100%
  EVIDENCE: ........................................................................ [ 95%] | .............................                                            [100%]

- [x] G6: The production dependency graph is deployed with PostgreSQL and the existing Temporal service, migrations applied, workers running, and health/readiness checks green.
  CHECK: uv run python scripts/verify_production_deployment.py
  EXPECT: production deployment verification passed
  EVIDENCE: production deployment verification passed

- [ ] G7: Calendar and e-signature providers execute authorized availability, booking, replay/idempotency, reconciliation, signature presentation, completion, failure, and recovery end to end against configured production providers.
  EVIDENCE: pending — requires explicitly authorized provider accounts, payloads, recipients, and external-effect execution

- [ ] G8: Cognitive production routing executes configured provider-backed routes, credential lifecycle, grounding/safety gates, durable recovery, and the complete evaluation suite end to end.
  EVIDENCE: pending — requires explicitly authorized production cognitive provider invocation and evaluation evidence

- [ ] G9: Ingress, outbound senders, connectors, webhooks, reconciliation, compensation, nurture, representation, transaction, and durable domain workflows execute end to end with provider evidence and replay safety.
  EVIDENCE: pending — requires explicitly authorized provider ingress/sender and external-effect execution

- [ ] G10: The complete operator API, web UI workspace, and native iOS offline/reauthentication surface are implemented, built, and verified against their contracts.
  CHECK: uv run python scripts/verify_ios_surface.py
  EXPECT: /iOS surface verification passed/
  EVIDENCE: pending

- [ ] G11: PostgreSQL backup, restore, artifact durability, and post-restore application verification pass against the deployed production database.
  EVIDENCE: pending — backup/restore requires a named artifact and explicitly authorized destructive restore target

- [x] G12: Temporal workflows pass replay, restart, timeout, delayed webhook, compensation, and recovery verification against the deployed Temporal service.
  CHECK: TEMPORAL_ADDRESS=127.0.0.1:7233 TEMPORAL_NAMESPACE=default uv run python scripts/check_temporal.py
  EXPECT: Temporal connectivity verification passed
  EVIDENCE: Temporal connectivity verification passed

- [x] G13: The complete production candidate/evaluation suite passes with recorded results, thresholds, provenance, and no disabled capability silently omitted.
  CHECK: uv run python scripts/run_evaluations.py --candidates evaluations/production-candidates.json
  EXPECT: passed": true
  EVIDENCE: "suiteVersion": "production-safety/1.0.0" | }

- [ ] G14: Every governing production gate has independently reconstructable evidence and all release predicates are satisfied.
  CHECK: uv run python scripts/verify_gate_registry.py
  EXPECT: gate registry verification passed
  EVIDENCE: pending

- [ ] G15: The repository is actually released only after all gates above are checked with non-pending evidence and the authoritative completion ledger reports release_complete true.
  CHECK: uv run python scripts/verify_completion_ledger.py
  EXPECT: completion ledger verification passed
  EVIDENCE: pending

<!--
This file intentionally covers the entire production objective. Do not delete,
weaken, reorder, or replace a gate with a narrower test. If a requirement is
genuinely impossible, add ABANDON: <gate id> <reason> and report it; never hide
it by changing scope.
-->
