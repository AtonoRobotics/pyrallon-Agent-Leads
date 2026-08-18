# Residential Buyer Operations Ontology v0 Contract

**Ontology version:** `buyer-ops/0.1.0`  
**Status:** Governing executable semantic contract, Revision 1  
**Jurisdiction profile:** Texas, effective-law profile beginning January 1, 2026  
**Canonical store:** PostgreSQL

## 1. Purpose and authority

This ontology defines canonical entities, relationships, epistemic types, state machines, predicates, and transition requirements used by the CRM, workflows, context compiler, knowledge system, UI, analytics, Habitat, connectors, evaluations, and projections.

The ontology is not a prompt glossary. Canonical writes must validate against its generated schemas and transition rules. PostgreSQL is authoritative; graph, vector, summary, model, and provider representations are derived.

## 2. Universal record envelope

Every canonical record contains:

```ts
interface CanonicalRecord {
  id: string;                 // UUIDv7
  tenantId: string;
  schemaVersion: "buyer-ops/0.1.0";
  recordType: string;
  version: number;            // monotonic per record
  createdAt: string;
  updatedAt: string;
  effectiveFrom: string;
  effectiveTo?: string;
  createdBy: ActorRef;
  sourceEvidenceIds: string[];
  supersedesId?: string;
  status: string;
}
```

Rules:

- IDs are opaque and tenant-scoped in every query.
- Updates use optimistic version comparison or the declared serializable resource lock.
- Historical values are closed by `effectiveTo`; they are not overwritten without transition evidence.
- `sourceEvidenceIds` is non-empty for externally consequential facts, consent, representation, agreements, appointments, and provider results.
- Deletion produces a non-personal tombstone and projection fence before derived-store purge.

## 3. Canonical entity catalog

| Type | Purpose | Required distinguishing fields |
|---|---|---|
| `Tenant` | Customer isolation and deployment boundary | deployment mode, locale, policy profile |
| `Brokerage` | Sponsoring/contracting brokerage | legal name, license identity, policy profile |
| `LicenseHolder` | Broker, associated broker, or sales agent | license number/type, sponsoring broker, active interval |
| `ServicePrincipal` | Product-owned executing identity | principal type, credential bindings, allowed capabilities |
| `Person` | Deduplicated natural person | canonical identity state, names, verified endpoints |
| `ContactEndpoint` | Email, phone, or other address | type, normalized value, verification, ownership, status |
| `BuyingParty` | One or more people acting in a purchase decision | members, roles, decision authority |
| `BuyerJourney` | One purchase objective over time | buying party, territory, journey states, owner |
| `Conversation` | Participant/channel-specific interaction stream | participants, channel, external thread refs |
| `Message` | Inbound/outbound communication | direction, sender, recipients, body artifact, delivery state |
| `ConsentGrant` | Purpose/channel-scoped communication permission | subject, channel, purpose, basis, scope, validity |
| `Suppression` | Contact prohibition | subject/endpoint, scope, reason, effective time |
| `LeadSource` | Origin and attribution | source type, campaign, parameters |
| `QualificationCriterion` | Versioned question/criterion definition | purpose, allowed states, freshness |
| `QualificationObservation` | Buyer-specific value/state | criterion, epistemic item, status |
| `BuyerRequirement` | Buyer-stated property/process constraint | predicate, value/range, priority, temporal validity |
| `FinancingReadiness` | Bounded operational financing state | state, verification source, freshness; not loan qualification |
| `Appointment` | Consultation or other meeting | type, participants, resource, interval, provider refs, state |
| `Commitment` | Promised action or follow-up | obligor, beneficiary, due time, evidence, state |
| `PropertyReference` | Manually supplied property identifier | address/source/time; no completeness/listing-status inference |
| `DocumentArtifact` | Versioned evidence-bearing document | digest, storage ref, form/template version, retention class |
| `IabsDelivery` | Delivery of current IABS notice | fields in §7 |
| `WrittenBuyerAgreement` | Representation or showing-only writing | fields in §8 |
| `AgreementQualification` | Action-specific agreement prerequisite result | fields and algorithm in §9 |
| `RepresentationRelationship` | Temporal representation state | broker, buying party, agreement, scope, status |
| `Transaction` | Manually initiated under-contract record | parties, property ref, executed artifact, confirmed dates |
| `TransactionMilestone` | Confirmed or proposed milestone | type, due time, confirmation, state |
| `Authorization` | Scoped operational authority | grantor, grantee, action class, resource, expiry, revocation |
| `Approval` | Human decision over exact proposed payload | approver, digest, scope, expiry, decision |
| `EffectAttempt` | Durable provider-changing attempt | intent, permit digest, idempotency, state, receipt |
| `Evidence` | Source observation or artifact | source, digest, retention, captured time |
| `Assertion` | Attributed statement not yet verified | speaker/source, proposition, temporal applicability |
| `VerifiedFact` | Evidence-backed proposition admitted by rule | verification method, sources, validity |
| `Inference` | Derived proposition | method/model, inputs, confidence, expiry |
| `Memory` | Recall aid linked to sources | scope, summary, source links, invalidation state |
| `Contradiction` | Incompatibility between epistemic items | item refs, scope, resolution state |
| `Correction` | Attributed correction/supersession | corrected item, replacement, reason |
| `WorkflowReference` | Link to durable execution | workflow/run IDs, type, current execution state |

## 4. Typed relationship catalog

Every relationship has `tenantId`, `relationshipType`, subject/object IDs, effective interval, evidence, status, and ontology version.

| Relationship | Subject → Object | Cardinality/invariant |
|---|---|---|
| `MEMBER_OF_BUYING_PARTY` | Person → BuyingParty | Many-to-many, role required |
| `HAS_JOURNEY` | BuyingParty → BuyerJourney | One-to-many |
| `OWNS_JOURNEY` | LicenseHolder → BuyerJourney | Exactly one current operational owner |
| `SPONSORED_BY` | LicenseHolder → Brokerage | At most one active sponsoring brokerage for a sales agent |
| `PARTICIPATES_IN` | Person/LicenseHolder/ServicePrincipal → Conversation | Many-to-many, participant role required |
| `CONCERNS_JOURNEY` | Conversation → BuyerJourney | At most one primary journey; cross-journey links explicit |
| `USES_ENDPOINT` | Person/ServicePrincipal → ContactEndpoint | Ownership/authorization status required |
| `CONSENTS_TO` | Person → ConsentGrant | Purpose/channel scoped |
| `SUPPRESSED_BY` | Person/Endpoint → Suppression | Active suppression dominates consent |
| `STATES_REQUIREMENT` | Person → BuyerRequirement | Assertion provenance required |
| `HAS_APPOINTMENT` | BuyerJourney → Appointment | One-to-many |
| `OWES_COMMITMENT` | Actor → Commitment | Exactly one obligor |
| `BENEFITS_FROM_COMMITMENT` | Commitment → Person/BuyingParty | At least one beneficiary |
| `DELIVERED_TO` | IabsDelivery → Person | Exactly one recorded recipient per delivery event |
| `ISSUED_BY` | IabsDelivery → LicenseHolder | Exactly one responsible license holder |
| `PARTY_TO_AGREEMENT` | Brokerage/BuyingParty → WrittenBuyerAgreement | Exactly one broker; one or more buyer parties |
| `RESPONSIBLE_FOR_AGREEMENT` | LicenseHolder → WrittenBuyerAgreement | One responsible license holder; does not replace broker party |
| `QUALIFIES_ACTION` | AgreementQualification → Proposed domain action | Exactly one action intent/digest |
| `SUPPORTED_BY_AGREEMENT` | AgreementQualification → WrittenBuyerAgreement | Zero only for explicit exception result |
| `REPRESENTS` | Brokerage → BuyingParty | Requires valid representation agreement and effective interval |
| `REFERS_TO_PROPERTY` | Journey/Appointment/Transaction/Agreement → PropertyReference | Source/time retained |
| `SUPPORTED_BY` | Fact/Inference/Memory/Action → Evidence/EpistemicItem | At least one where contract requires grounding |
| `CONTRADICTS` | EpistemicItem ↔ EpistemicItem | Symmetric semantic relationship; two directed projection edges allowed |
| `SUPERSEDES` | Record → Record | Same semantic subject; acyclic chain |

## 5. Epistemic contract

```ts
type EpistemicType = "evidence" | "assertion" | "verified_fact" | "inference" | "memory";

interface Proposition {
  subjectRef: string;
  predicate: string;
  value: unknown;
  applicableJourneyId?: string;
  validFrom: string;
  validTo?: string;
}
```

### Allowed transitions

| From | To | Required mechanism |
|---|---|---|
| Evidence | Assertion | Attributed extraction with source span/artifact location |
| Assertion | VerifiedFact | Predicate-specific verification rule and allowed evidence |
| Evidence/Assertion/Fact | Inference | Versioned derivation with input IDs, method, confidence, expiry |
| Any source-linked set | Memory | Versioned compaction/summary retaining every material source link |
| Any item | Corrected/superseded item | Correction evidence and explicit replacement/invalidation |

Prohibited transitions:

- model output directly to `VerifiedFact`;
- vector similarity or graph adjacency directly to canonical relationship;
- memory directly to consent, representation, authority, approval, completed action, or provider outcome;
- absence of data to a negative fact unless the predicate defines closed-world semantics;
- stale or superseded item to current context without explicit historical labeling.

## 6. Orthogonal state machines

No single funnel field governs behavior. The following machines coexist.

### 6.1 Contactability

`unknown → contactable → temporarily_unavailable → suppressed | invalid`

- Active suppression dominates every other state.
- Endpoint verification and person-level consent remain separate.

### 6.2 Qualification

`not_started → collecting → sufficient_for_consult → stale | contradicted`

- `sufficient_for_consult` is computed from the versioned consultation policy.
- A declined criterion is not unknown; both may still be policy-permissible.

### 6.3 Representation

`unconfirmed → not_represented → agreement_pending → represented | non_representation_showing_only → expired | terminated | conflict`

- `represented` requires a valid representation agreement.
- `non_representation_showing_only` does not create representation.
- Conflicting current agreements enter `conflict` and block dependent effects.

### 6.4 Buyer journey

`captured → contacted → qualifying → nurture | consultation_ready → consultation_booked → representation_pending → represented → searching → under_contract → closed`

Terminal/side states: `suppressed`, `ineligible`, `released`, `dormant`, `blocked`.

Journey state is a summary. Action eligibility is computed from orthogonal state, evidence, policy, and authority.

### 6.5 Appointment

`proposed → held → provider_pending → confirmed → completed | cancelled | no_show | unknown_outcome`

- Provider timeout enters `unknown_outcome`; reconciliation precedes retry.
- `confirmed` requires provider resource ID/version and participant mapping.

### 6.6 Agreement execution

`draft → agent_approved → presented → partially_signed → executed → effective → expired | terminated | superseded | void`

- `agent_approved` is required before presentation when individualized terms exist.
- Only `effective` can support an `AgreementQualification`.
- Execution evidence includes every required signature and immutable artifact digest.

## 7. `IabsDelivery` schema

```ts
interface IabsDelivery extends CanonicalRecord {
  recordType: "IabsDelivery";
  formId: string;
  formVersion: string;
  jurisdiction: "TX";
  responsibleLicenseHolderId: string;
  brokerageId: string;
  recipientPersonId: string;
  deliveryChannel: "email_attachment" | "email_link" | "in_person" | "document_portal" | "other_approved";
  deliveredAt: string;
  trigger: "first_substantive_specific_property_communication" | "showing_prerequisite" | "brokerage_earlier_delivery";
  propertyReferenceId?: string;
  artifactId: string;
  artifactDigest: string;
  providerReceiptId?: string;
  evidenceIds: string[];
  validityState: "delivered" | "delivery_unknown" | "superseded" | "invalid";
}
```

`delivery_unknown` does not satisfy a prerequisite requiring proven delivery. Posting an IABS on a website is represented separately and does not itself prove delivery to a party.

## 8. `WrittenBuyerAgreement` schema

```ts
type AgreementType = "representation" | "non_representation_showing";

interface WrittenBuyerAgreement extends CanonicalRecord {
  recordType: "WrittenBuyerAgreement";
  agreementType: AgreementType;
  jurisdiction: "TX";
  brokerPartyId: string;
  responsibleLicenseHolderId: string;
  buyerPartyIds: string[];
  serviceDefinitions: ServiceDefinition[];
  propertyScope?: PropertyScope;
  exclusivity: "exclusive" | "non_exclusive";
  effectiveAt: string;
  terminatesAt: string;
  compensation: {
    amountOrRate?: string;
    determinationMethod?: string;
    objectivelyAscertainable: boolean;
    negotiabilityDisclosurePresent: boolean;
  };
  signatureEvidence: SignatureEvidence[];
  executedArtifactId: string;
  executedArtifactDigest: string;
  executionState: "draft" | "agent_approved" | "presented" | "partially_signed" | "executed" | "effective" | "expired" | "terminated" | "superseded" | "void";
  terminationReason?: string;
}
```

Validation rules:

1. `brokerPartyId`, at least one buyer party, responsible license holder, services, termination time, compensation determination, negotiability disclosure, and signature evidence are required before `effective`.
2. `terminatesAt > effectiveAt`.
3. For `non_representation_showing`:
   - `exclusivity == non_exclusive`;
   - `terminatesAt <= effectiveAt + 14 days`;
   - service definitions contain showing access only;
   - advice, opinions, negotiation, offer presentation, transaction coordination, and other buyer brokerage services are prohibited;
   - the system must not create a `RepresentationRelationship`.
4. For `representation`, the agreement may limit services but must pass current brokerage and non-waivable-duty policy.
5. Supersession closes the prior effective interval atomically with activation of the successor.

## 9. `AgreementQualification` and Habitat predicate

```ts
type AgreementControlledAction = "residential_showing" | "residential_offer_presentation";

interface AgreementQualification extends CanonicalRecord {
  recordType: "AgreementQualification";
  actionType: AgreementControlledAction;
  actionIntentId: string;
  actionPayloadDigest: string;
  buyerPartyId: string;
  responsibleLicenseHolderId: string;
  brokerageId: string;
  propertyReferenceId: string;
  evaluatedAt: string;
  policyVersion: string;
  agreementId?: string;
  agreementVersion?: number;
  iabsDeliveryId?: string;
  exceptionCode?: "listing_brokerage_open_house" | "other_approved_statutory_exception";
  result: "qualified" | "denied" | "requires_resolution";
  reasons: string[];
  expiresAt: string;
}
```

### Qualification algorithm

Habitat computes this result from canonical state; it never trusts a proposal-supplied result.

1. Load tenant, buyer party, property, responsible license holder, brokerage, current policy, agreements, IABS evidence, representation conflicts, and action payload under one attributable version vector.
2. If a policy-recognized statutory exception applies, verify its required evidence and brokerage permission; emit a short-lived `qualified` result bound to the action digest.
3. Otherwise require one current effective agreement covering the buyer, broker, action time, and property/service scope.
4. For `residential_showing`, allow either an effective representation agreement covering the showing or a valid `non_representation_showing` agreement.
5. For `residential_offer_presentation`, allow only an effective representation agreement whose services cover offer presentation. A `non_representation_showing` agreement always denies.
6. Require IABS delivery when the applicable trigger and policy require it; unknown delivery denies.
7. Any conflicting agreement, revoked/terminated agreement, expired term, missing signature/artifact digest, version mismatch, unapproved exception, or changed action digest denies or requires resolution.
8. Bind the result to the exact action intent, payload digest, canonical version vector, and short expiry.
9. DW2-C1 re-evaluates qualification immediately before connector/provider invocation. The qualification record alone is not an effect permit.

### Open-house exception handling

The ontology supports narrow exception evidence because Texas treatment differs based on who hosts the open house and whom that license holder represents. The product must not infer listing-brokerage membership or seller representation from text. Required brokerage/license/property relationships must be canonical and current. A brokerage policy may disable an otherwise available exception.

## 10. Consent and suppression predicates

`MayContact(person, endpoint, channel, purpose, at)` is true only when:

- identity mapping is resolved or policy permits the identified endpoint state;
- an applicable consent/legal-basis record is active;
- no active suppression dominates the requested scope;
- frequency, quiet-hour, operating-hour, and channel rules permit the action;
- representation or conflict state does not prohibit contact; and
- the exact tenant/agent/broker principal is authorized.

Opt-out writes suppression synchronously before acknowledgment completion. Suppression enforcement is deterministic and never depends on cognition.

## 11. Fair-housing feature semantics

Every feature made available to an optimizer is a typed `OperationalFeature` with:

- feature ID/version;
- source predicates and transformations;
- allowed purposes/action classes;
- prohibited purposes;
- freshness;
- whether buyer-stated or independently derived;
- proxy-risk classification;
- policy owner; and
- decision/promotion lineage.

Geography, budget, financing, timing, and property requirements are allowed only for their declared buyer-service purposes. They cannot be transformed to infer protected traits or used to reduce minimum service, ranking access, cadence, response, availability, escalation, or opportunity.

## 12. Canonical transition admission

| Transition class | Required admission |
|---|---|
| Deterministic observation | Schema, tenant, source identity, idempotency, evidence |
| Cognitive proposal | Proposal schema, context sufficiency, grounding, policy, expiry; then domain-specific admission |
| Consent/representation/authority | Current principal, explicit evidence, policy, version, revocation checks |
| External completion | Provider receipt or reconciliation evidence; intent alone is insufficient |
| Fact verification | Predicate-specific allowed evidence and verifier independent of model opinion |
| Correction/supersession | Corrected item, replacement/invalidation, attribution, affected-context invalidation |

## 13. Projection contract

- PostgreSQL emits canonical changes with tenant, sequence, ontology version, record version, and tombstone/fence state.
- Neo4j edges reproduce only declared relationship types and temporal intervals.
- pgvector entries retain canonical source IDs, purpose scope, ontology version, and projection epoch.
- Retrieval begins from an authorized deterministic candidate set and checks projection fences independently.
- Rebuild follows DW2-C2; no projection may reinterpret stored cases under a new ontology without migration and validation.

## 14. Schema evolution

Changes use semantic versions:

- Patch: clarification or backward-compatible validation tightening with no stored semantic reinterpretation.
- Minor: additive type/field/relationship with explicit defaults or migration.
- Major: incompatible meaning, cardinality, state, or transition change.

Every change requires:

1. migration and rollback/forward-repair plan;
2. affected context, workflow, connector, policy, UI, graph/vector, analytics, and evaluation updates;
3. replay of representative and adversarial stored cases;
4. proof that consent, authority, agreement, epistemic, deletion, and isolation invariants do not regress; and
5. explicit activation and projection cutover evidence.

## 15. Acceptance tests

1. A BuyingParty with multiple people and multiple journeys reconstructs without collapsing participant or journey state.
2. Unknown, declined, stale, contradicted, inferred, and verified qualification values remain distinguishable through compaction and projection rebuild.
3. A model assertion cannot write `VerifiedFact` without a predicate-specific verification transition.
4. Active suppression defeats an otherwise active consent grant before the next outbound effect.
5. A representation agreement requires broker party, buyer parties, services, term, compensation determination, negotiability disclosure, signatures, and artifact digest.
6. A non-representation showing agreement longer than 14 days, exclusive, or containing advice/other services is rejected.
7. A non-representation showing agreement cannot create representation or qualify offer presentation.
8. Showing admission fails with no agreement, expired agreement, conflict, changed property/action digest, missing required IABS evidence, or unapproved exception.
9. Offer-presentation admission fails without a covering representation agreement.
10. A valid narrow open-house exception passes only with canonical listing-brokerage/seller-representation evidence and enabled brokerage policy.
11. Agreement revocation or supersession committed before effect-attempt creation denies the effect; a permitted prior attempt retains attributable ordering.
12. Deletion/revocation fences prevent retrieval and re-derivation during projection rebuild.
13. Buyer-stated operational features remain usable for declared service purposes but fail optimizer compilation when repurposed as prohibited proxies.
14. Every externally consequential context packet identifies ontology version and source record versions.
15. Migration replay produces declared equivalent or explicitly migrated semantics and cannot silently reinterpret prior facts or agreements.

## 16. Authoritative Texas sources

- Texas Real Estate Commission, “What Changes in 2026 About Buyer/Tenant Representation in Texas”: https://www.trec.texas.gov/article/what-changes-2026-about-buyertenant-representation-texas
- Texas Real Estate Commission, “Information About Brokerage Services Form”: https://www.trec.texas.gov/information-about-brokerage-services-form
- Texas Occupations Code, TRELA §§1101.562–1101.563, linked from the TREC guidance above.

This contract encodes product controls from authoritative guidance but does not authorize the AI to practice law or independently interpret disputed legal facts. Brokerage-approved policy may narrow product behavior.

