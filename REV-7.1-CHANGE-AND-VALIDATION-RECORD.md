# Revision 7.1 Change and Validation Record

**Status:** Accepted design baseline for contract-driven non-cognitive implementation  
**Validated artifacts:** PRD 7.1, Cognitive Gateway 1.1, Design-Truth Ledger 1, Ontology v0.1, Gate Registry 1, OT-01 Contract 1, Implementation Packet Index 1

## 1. Changes closed

| Finding | Root correction | Affected artifacts | Closure evidence |
|---|---|---|---|
| Habitat and Temporal both owned wakes/leases/lifecycle | Temporal now exclusively owns signals, wakes, timers, workflow/activity leases, workflow concurrency, retries, compensation, recovery, and worker lifecycle; Habitat owns deterministic admission/authority/effect permit functions | PRD FR-20, Design Ledger §5, OT-01 §4–§5, PKT-03/04 | Ownership search and cross-artifact semantic review |
| Texas agreement types absent | Added canonical `IabsDelivery`, `WrittenBuyerAgreement`, and `AgreementQualification`; encoded representation and non-representation showing constraints | PRD FR-16/20, Ontology Contract §§7–9, Ontology JSON Schema, Ledger DT-030–035 | Current TREC source verification and schema/contract review |
| Showing/offer prerequisite was not mechanically enforced | Added current-state Habitat predicate bound to action/payload/version/expiry; narrow exceptions require canonical evidence and brokerage policy | PRD FR-20, Ontology §9, PKT-03 | Admission algorithm and acceptance scenarios |
| Fair-housing optimization was evaluative only | Added feature allowlists, immutable prohibited-proxy rules, non-optimizable service floors/caps, counterfactual tests, service-parity promotion, full lineage | PRD FR-12/11.7, Ledger DT-060–064, Gate 007, OT-01 §10/§18 | Gate evidence contract and promotion requirements |
| Acceptance gates were monolithic | Retained all 35 and classified them as PI, OT, CAP, or CP with machine-readable applicability, dependencies, evidence, and blocks | PRD §11, Gate Registry | YAML parse; exactly 35 ordered unique gates; dependency DAG |
| Prohibited was proposed as an approval level | Kept `requiredApproval` limited to none/agent/broker; added `policyDisposition`; prohibited actions cannot be approved or dispatched | Gateway Contract §§5/17, Gateway JSON Schema, Ledger DT-015 | Schema relationship and acceptance tests 17 |
| Proposal staleness not mechanically bounded | Proposal and every proposed action now require hard expiry; Habitat independently checks freshness and current truth | Gateway Contract §§2/5/17, Gateway JSON Schema, PKT-03 | Required schema fields and rejection tests |
| Hosted-agent writes could be waived by routing | Hosted agents with inseparable writes are route-ineligible and configuration cannot waive the rule | PRD FR-13.10, Gateway §§8/12/17 | Negative eligibility tests |
| Codex SDK versus `codex exec` ambiguous | Each action-class route pins one transport; the other is a separately qualified route, never opportunistic selection | PRD FR-13.2, Gateway §9/§17, Ledger DT-042, PKT-07 | Route readback and transition tests |

## 2. Source verification

Current Texas Real Estate Commission guidance confirms:

- the 2026 written-agreement requirements apply beginning January 1, 2026;
- a license holder working with a prospective residential buyer must have a qualifying written agreement before showing residential property, or—if no property will be shown—before presenting an offer;
- the agreement may be a representation agreement or a qualifying non-representation showing-only agreement where applicable;
- non-representation showing agreements are non-exclusive, no longer than 14 days, and do not permit opinions, advice, or other buyer brokerage services; and
- IABS delivery is required at the first substantive communication concerning specific real property, subject to the applicable legal scenario.

Sources:

- https://www.trec.texas.gov/article/what-changes-2026-about-buyertenant-representation-texas
- https://www.trec.texas.gov/information-about-brokerage-services-form

## 3. Structural validation results

| Check | Result |
|---|---|
| Governing JSON artifacts parse | Pass |
| All internal JSON Schema `$ref` targets exist | Pass |
| Gate YAML parses | Pass |
| Gate IDs | 35 ordered, unique IDs (`GATE-001`–`GATE-035`) |
| Gate classes/evidence/blocking fields | Present for every gate |
| Gate dependencies | All references resolve; graph is acyclic |
| PRD gate classifications | Gates 1–35 classified exactly once |
| OT-01 gate references | All resolve to registry IDs |
| Implementation packet gate references | All resolve to registry IDs |
| Design decisions | 43 unique `DT` declarations |
| Open deployment decisions | 9 unique `OPEN` declarations |
| Governing local artifact references | All referenced files exist |
| Markdown code fences | Balanced |
| Stale governing revision labels | Absent |

PKT-00 still requires compilation with the implementation-selected Draft 2020-12 validator, generated-type drift checks, and valid/invalid golden fixtures before application code can consume the schemas.

## 4. Contractability verdict

The design is contractable for:

- schema/type generation;
- canonical PostgreSQL CRM and epistemic model;
- evidence ledger and artifact references;
- Habitat admission and no-effect permit APIs;
- Temporal workflow foundation;
- deterministic ingress, identity, consent, suppression, and acknowledgment;
- context compiler and cognitive gateway interfaces in no-effect mode;
- operator exception/decision surface; and
- gate harness and activation controller.

The artifacts do not require an implementation agent to invent component ownership, cognitive authority, provider fallback semantics, agreement prerequisites, fair-housing optimization authority, or gate applicability.

## 5. Residual activation decisions

No unresolved architecture blocker remains for the non-cognitive foundation. The nine `OPEN` decisions in the Design-Truth Ledger remain required at their stated activation boundaries:

1. Customer/broker authority and automation policy
2. Cognitive route matrix and identities
3. Initial email/calendar/document provider enablement
4. Paid acquisition authority
5. Approved agreements/templates/compensation language
6. Exact service zones, locations, and capacity
7. Historical outcome baseline
8. Approved bilingual knowledge corpus
9. Retention/legal-hold/deletion/object-lock settings

An unresolved deployment choice is a typed configuration blocker, not permission to hard-code or defer the governing capability.

## 6. Release condition

The design package is accepted as the baseline for PKT-00 through PKT-04. Live capability activation remains governed by `PRODUCTION-GATE-REGISTRY.yaml`, the relevant open-decision closure, production-equivalent provider evidence, and independent completion reconstruction.

