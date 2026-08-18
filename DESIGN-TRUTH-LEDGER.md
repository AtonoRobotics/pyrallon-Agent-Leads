# Design-Truth Ledger

**System:** Autonomous Buyer Agent Operations System  
**Ledger revision:** 1  
**Governing baseline:** PRD Revision 7.1  
**Purpose:** Authoritative record of product and architecture decisions that implementation contracts may rely on without reinterpretation

## 1. Decision precedence

When artifacts conflict, use this order:

1. Platform invariants and explicit authority prohibitions in PRD Revision 7.1.
2. Executable contracts referenced by the PRD.
3. This ledger's approved decisions.
4. Versioned deployment, brokerage, agent, and buyer configuration within their permitted scope.
5. Implementation ADRs that do not change higher-order semantics.
6. Framework defaults, provider behavior, model output, reused code, and examples.

An implementation convenience cannot override a higher-ranked source. A new conflict enters `decision_blocked`; it is not resolved silently.

## 2. Governing artifact set

| Artifact | Governs | Status |
|---|---|---|
| `REAL-ESTATE-BUYER-AGENT-AI-PRD.md` Revision 7.1 | Product scope, requirements, authority, acceptance | Governing |
| `COGNITIVE-RUNTIME-GATEWAY-CONTRACT.md` Revision 1.1 | Cognitive request, routing, adapters, credentials, proposal boundary | Governing |
| `ONTOLOGY-V0-CONTRACT.md` Revision 1 | Canonical entity, relationship, epistemic, agreement, and transition semantics | Governing |
| `ONTOLOGY-V0.schema.json` Version 0.1.0 | Executable structural schemas for OT-01 and agreement-critical canonical records | Governing schema |
| `COGNITIVE-RUNTIME-GATEWAY.schema.json` Version 1.1.0 | Executable cognitive request/proposal boundary schemas | Governing schema |
| `PRODUCTION-GATE-REGISTRY.yaml` Revision 1 | Gate applicability, dependencies, evidence, activation blocks | Governing |
| `OPERATIONAL-THREAD-01-LEAD-TO-CONSULT-CONTRACT.md` Revision 1 | First production operational thread | Governing for that thread |
| `IMPLEMENTATION-PACKET-INDEX.md` Revision 1 | Dependency-bounded autonomous implementation sequence | Governing implementation plan |
| `REV-7.1-CHANGE-AND-VALIDATION-RECORD.md` Revision 1 | Change closure, structural validation, contractability, residual decisions | Validation record |

## 3. Mission and boundary decisions

| ID | Decision | Status | Enforcement | Evidence |
|---|---|---|---|---|
| DT-001 | The product autonomously operates non-property-search work for Texas residential buyer representation. | Approved | Domain/API boundaries and exclusions | PRD FR-1–FR-10 and §10 tests |
| DT-002 | The primary customer and operator is an independent real-estate agent; sponsoring-broker policy remains authoritative where applicable. | Approved | Tenant/principal model and policy hierarchy | Tenant configuration and authority tests |
| DT-003 | Initial service geography is configurable and initially covers San Antonio, Fredericksburg/Hill Country, and Austin areas. | Approved with deployment detail open | Versioned service-zone configuration | Configuration readback and routing tests |
| DT-004 | No MLS, IDX, portal scraping, or licensed property-data integration exists initially. | Approved | Capability absence and property-claim validator | Negative tool inventory and grounded-answer tests |
| DT-005 | The business north star is attributable lift in closed buyer transactions; intermediate funnel metrics remain necessary operational measures. | Approved | Analytics semantic contract | Cohort and attribution reconciliation |
| DT-006 | Production capabilities are activated by applicable gates; capability sequencing does not delete complete-product scope. | Approved | Gate registry and activation controller | Gate readback and denied activation tests |

## 4. Principal, identity, and authority decisions

| ID | Decision | Status | Enforcement | Evidence |
|---|---|---|---|---|
| DT-010 | The field-visible AI identity is one Buyer Operations Agent. Internal specialists are implementation detail. | Approved | UI/API identity policy | Conversation and attribution traces |
| DT-011 | The human or business principal, responsible agent/broker, executing service identity, connector grant, and action delegation are distinct typed identities. | Approved | Identity/authority schemas | Effect evidence package |
| DT-012 | Cognitive components propose; they never possess external write authority. | Approved invariant | Gateway tool surface and Habitat | Negative capability and penetration tests |
| DT-013 | Every provider-changing effect requires just-in-time Habitat admission and a single-use effect permit. | Approved invariant | Habitat + connector gateway | DW2-C1 race and replay tests |
| DT-014 | Workflow or run admission never grants continuing authority. | Approved invariant | Permit expiry/version binding | Revocation and mutation race tests |
| DT-015 | Prohibited actions are not approvable. Human approval cannot override a platform prohibition. | Approved invariant | Proposal validation and Habitat policy | Prohibited-disposition tests |
| DT-016 | Human approval is reserved for licensed judgment, individualized agreements/terms, policy changes, and exceptional authority—not routine deterministic work. | Approved | Action-class policy | Approval-rate and exception audit |

## 5. Component ownership

| Concern | Sole owner | Contract boundary |
|---|---|---|
| Canonical business state and typed relationships | PostgreSQL | Transactional domain repositories |
| Source artifacts | Encrypted object storage | Digest-addressed artifact API and retention policy |
| Durable workflow signals, wakes, timers, leases, retries, compensation, recovery, workflow concurrency, worker lifecycle | Temporal | Versioned workflow/activity contracts |
| Event-schema and tenant admission | Habitat | `AdmitEvent` API |
| Current policy and authority evaluation | Habitat | `EvaluateAuthority` API |
| Effect admission, permit issuance/redemption, idempotency registration | Habitat + PostgreSQL | DW2-C1 |
| External provider invocation | Governed connector gateway | Current permit required |
| Context assembly | Context compiler | Versioned context manifest and packet |
| Cognitive route selection and invocation normalization | Cognitive Runtime Gateway | Cognitive Gateway 1.1 |
| Model/provider protocol translation | Runtime adapter | Adapter interface only |
| Graph and semantic projections | Projection services | PostgreSQL epochs/fences and rebuild contracts |
| User-visible decisions and exceptions | Agent web/iOS surfaces | Canonical API; no local authority expansion |

### Ownership prohibitions

- Temporal does not own business truth, policy authority, or provider-changing permission.
- Habitat does not own workflow clocks, wakes, leases, retries, compensation, worker lifecycle, or business truth.
- The cognitive gateway does not own workflows, context truth, memory, connectors, or policy.
- Connectors do not infer authority from possession of credentials.
- Neo4j, pgvector, summaries, runtime threads, and provider memory do not own canonical facts.

## 6. Data and epistemic decisions

| ID | Decision | Status | Enforcement | Evidence |
|---|---|---|---|---|
| DT-020 | PostgreSQL is the canonical internal CRM and ontology-instance store. | Approved invariant | Repository boundaries | Reconstruction tests without external CRM |
| DT-021 | Evidence, Assertion, Verified Fact, Inference, and Memory are distinct types with provenance and temporal validity. | Approved invariant | Ontology v0 schemas | Schema and transition tests |
| DT-022 | Model output, vector similarity, graph traversal, and memory recall cannot silently become fact. | Approved invariant | Fact-transition policy | Adversarial provenance tests |
| DT-023 | Neo4j and pgvector are rebuildable projections; deletion/revocation fences dominate rebuilding. | Approved | DW2-C2 | Concurrent rebuild/deletion tests |
| DT-024 | Conversation compaction is source-linked and reconstructable; current context is recompiled for every cognitive invocation. | Approved | Context compiler | Long-horizon correction/replay evaluations |
| DT-025 | Record retention is classified by data type; deletion propagates to every derived store without erasing required non-personal tombstones. | Approved; deployment periods open | Retention engine | Propagation and legal-hold tests |

## 7. Texas representation decisions

| ID | Decision | Status | Enforcement | Evidence |
|---|---|---|---|---|
| DT-030 | IABS delivery is a first-class event with form version, responsible license holder, recipient, channel, time, and evidence. | Approved | Ontology and delivery workflow | Artifact/receipt reconstruction |
| DT-031 | Written buyer agreements are first-class canonical objects with `representation` and `non_representation_showing` types. | Approved | Ontology v0 | Schema and state tests |
| DT-032 | A non-representation showing agreement is non-exclusive, no longer than 14 days, and permits only showing access without advice, opinions, or other buyer brokerage services. | Approved invariant | Agreement validator and Habitat | Boundary tests |
| DT-033 | Before a residential showing, or before offer presentation when no property will be shown, Habitat requires a current `AgreementQualification` unless a narrow evidence-backed legal/brokerage exception applies. | Approved invariant | Habitat policy predicate | Denied and exception-path tests |
| DT-034 | The broker is the agreement party; the sales agent is the responsible license holder. | Approved | Party/cardinality schema | Agreement reconstruction |
| DT-035 | Autonomous showing selection and offer creation/submission remain prohibited despite modeling agreement prerequisites. | Approved invariant | Capability/action prohibitions | Negative proposal/effect tests |

Sources: current TREC guidance for TRELA §§1101.562–1101.563 and the IABS form effective January 1, 2026. Brokerage counsel/policy may narrow authority but cannot widen statutory limits.

## 8. Cognitive-runtime decisions

| ID | Decision | Status | Enforcement | Evidence |
|---|---|---|---|---|
| DT-040 | The product owns a provider-neutral cognitive gateway supporting subscription, API, workspace/service-account, cloud-IAM, and local endpoints. | Approved | Gateway 1.1 | Adapter conformance tests |
| DT-041 | Codex SDK or fixed `codex exec` is the initial OpenAI subscription transport; raw App Server is not a product interface. | Approved | Route schema and dependency policy | Runtime inventory |
| DT-042 | Each action-class route pins exactly one primary transport. SDK/CLI selection is not opportunistic. | Approved; route assignments open | Route matrix | Configuration readback |
| DT-043 | Subscription and API routes are explicit and non-equivalent. Cross-class fallback requires prior authorization and qualification. | Approved | Route policy | Transition and rejection tests |
| DT-044 | Hosted workspace agents with inseparable write tools are ineligible. | Approved invariant | Capability eligibility | Negative route tests |
| DT-045 | Cognitive output is a closed, expiring proposal with grounded claims, unknowns, risks, disposition, and evidence. | Approved | Proposal schema | Schema/grounding/expiry tests |
| DT-046 | No model, prompt, harness, or provider change receives authority until action-class evaluations pass. | Approved invariant | Promotion gate | Regression-block tests |

## 9. Connector decisions

| ID | Decision | Status | Enforcement | Evidence |
|---|---|---|---|---|
| DT-050 | MCP is a capability boundary, not the authority boundary. | Approved | Tool inventory + Habitat | Tool/effect separation tests |
| DT-051 | Cognitive runtimes receive only action-class-qualified read/retrieval/draft capabilities. | Approved invariant | Compiled tool surface | Negative write-access tests |
| DT-052 | Provider webhooks/change notifications are authoritative event inputs where supported; polling alone is insufficient. | Approved | Ingress adapters | Event/reconciliation tests |
| DT-053 | Unknown provider outcomes reconcile before retry. | Approved invariant | EffectAttempt state machine | Timeout/duplicate tests |
| DT-054 | Google Workspace and Microsoft 365 are supported product capabilities; initial deployment enablement is configurable. | Approved product scope; deployment choice open | Connector contracts | Capability gates |
| DT-055 | Twilio and DocuSign are initial adapter selections, not domain dependencies. | Approved | Stable connector interfaces | Replacement tests |

## 10. Fair-housing and optimization decisions

| ID | Decision | Status | Enforcement | Evidence |
|---|---|---|---|---|
| DT-060 | Every optimization action uses an explicit feature allowlist; undeclared features are unavailable. | Approved invariant | Feature compiler | Feature-lineage tests |
| DT-061 | Protected traits and prohibited proxies cannot affect targeting, ranking, availability, service level, cadence, escalation, or suppression. | Approved invariant | Policy engine | Counterfactual and proxy tests |
| DT-062 | Buyer-stated geography, budget, financing, timing, and property constraints remain valid operational facts but cannot be repurposed as demographic proxies. | Approved | Purpose-bound features | Perturbation and lineage tests |
| DT-063 | Minimum service guarantees and maximum frequency/quiet-hour bounds are not optimizer-controlled. | Approved invariant | Immutable policy layer | Boundary mutation tests |
| DT-064 | Promotion requires service-parity, conversion, complaint, and rollback gates with complete feature/policy provenance. | Approved | Promotion controller | Gate evidence |

## 11. Configuration hierarchy

1. Platform invariants: immutable through product configuration.
2. Deployment configuration: infrastructure, enabled providers, capacity, retention class, locale.
3. Brokerage configuration: legal/compliance policy, services, agreements, communications, approval thresholds.
4. Agent configuration: availability, tone, service zones, consultation logistics, bounded preferences.
5. Buyer configuration: consent, channels, timing, stated requirements; may narrow but never widen higher authority.

Every setting has schema, owner, version, effective time, validation, audit history, and rollback. An absent required setting creates `configuration_incomplete`, not a hidden default.

## 12. Reuse decisions

| Candidate | Decision | Allowed extraction | Prohibited inheritance |
|---|---|---|---|
| Hermes | Concepts/selected adapters only after review | Provider registry, transports, credential-source patterns, explicit fallback | Second orchestrator, memory authority, write-capable agent loop, unsupported token reuse |
| `alphavector-core` | Small contract-compatible modules/concepts only | Typed intent, fail-closed parsing, model/credential separation | Filesystem truth, free-form routing, API-key-only credential shape, hard-coded providers, habitat lifecycle |
| LangChain/LangGraph | Bounded cognitive harness candidate | Tool loops, structured output, specialist delegation | Durable orchestration, canonical memory, authority, connector writes |
| Deep Agents | Optional bounded specialist after evaluation | Planning/research/document analysis where measured | Product kernel, workflow engine, policy judge, system of record |
| Temporal | Adopt for durable workflow ownership | Signals, timers, leases, retries, compensation, recovery | Canonical business truth or external authority |

## 13. Open deployment decisions

These do not block canonical non-cognitive implementation but block the named activation.

| ID | Decision required | Owner | Blocks |
|---|---|---|---|
| OPEN-001 | Initial customer is a broker or a sponsored sales agent; sponsoring broker's approved automation policy | Customer/broker | Live outbound and representation operations |
| OPEN-002 | Action-class cognitive route matrix, including fixed Codex SDK versus `codex exec`, API/local routes, identities, capacity, and fallback | Product operator | Live cognition |
| OPEN-003 | Initial Google Workspace/Microsoft 365 enablement and document-storage provider | Customer/operator | Corresponding connector activation |
| OPEN-004 | Paid acquisition channels, budgets, and publishing authority | Customer/broker | Paid advertising |
| OPEN-005 | Approved representation/non-representation forms, templates, signature workflow, compensation language | Broker/counsel | Agreement delivery and showing/offer workflow activation |
| OPEN-006 | Exact service-zone polygons, consultation locations, travel and capacity limits | Agent/broker | Live consult scheduling |
| OPEN-007 | Historical 180-day funnel baseline | Agent/product operations | Outcome-lift claims |
| OPEN-008 | Approved English/Spanish knowledge corpus, owners, effective dates, supersession process | Broker/product | Substantive live cognition |
| OPEN-009 | Retention periods, legal-hold authority, deletion SLO, object-lock selection | Broker/product | Production retention activation |

## 14. Implementation admission rule

An implementation packet may begin only when:

- every referenced decision is `Approved` or the open value is a typed configuration input;
- the packet names its applicable gate IDs;
- component ownership matches §5;
- schemas and failure states are versioned;
- no implementation agent must choose legal policy, authority, canonical truth, provider fallback, or product scope; and
- completion evidence can be reconstructed independently from repository, test, runtime, provider, and canonical records.

If any condition fails, the packet enters `design_blocked` with the unresolved ledger IDs.

