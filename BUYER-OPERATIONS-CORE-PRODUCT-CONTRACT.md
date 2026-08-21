# Buyer Operations Core Product Contract

Revision: 1.0.0
Applies to: Revision 7.1 product, ontology 0.3.0, Cognitive Runtime Gateway 1.1, OT-01
Status: governing product contract for implementation

## 1. Product priority

The product is an autonomous Buyer Operations Agent. The primary experience is a guided buyer journey, not manual CRM administration.

The system must autonomously capture, identify, qualify, nurture, schedule, and prepare the buyer journey while preserving licensed judgment and provider-effect authority boundaries.

A user must be able to operate the system without manually entering normal CRM fields.

## 2. Buyer Journey Wizard

Every buyer journey is represented by a step-by-step wizard driven by canonical state and current configuration.

Each step contains:

- step identifier and version;
- purpose;
- current state;
- required information;
- known values and their epistemic types;
- missing information;
- acceptable answer forms;
- source and freshness requirements;
- next-step candidates;
- blocking conditions;
- escalation owner;
- completion evidence.

The wizard selects the next step deterministically:

1. resolve identity and contactability;
2. obtain or confirm consent;
3. satisfy required qualification inputs;
4. evaluate freshness, contradiction, and service-zone/capacity constraints;
5. identify consultation readiness;
6. offer and book consultation;
7. prepare representation workflow;
8. continue nurture or exception handling.

The wizard never asks for information already present as a current, sufficiently fresh, evidence-backed record. It asks one coherent question at a time and explains the purpose when the answer affects readiness, service, consent, or scheduling.

Cognitive output may propose questions and explanations. The deterministic wizard policy selects the next step and controls state transitions.

## 3. Qualification

Qualification is continuous and autonomous.

The agent must:

- identify required criteria from the active qualification policy;
- ask only missing or stale questions;
- distinguish buyer-stated facts, assertions, inferences, and unknowns;
- detect contradictions;
- determine consultation readiness;
- identify urgent escalation;
- avoid protected-class and prohibited-proxy features;
- record source, freshness, confidence, and policy version for every qualification observation.

The qualification decision must expose:

- ready or not-ready state;
- exact required inputs;
- satisfied inputs;
- missing or stale inputs;
- contradiction codes;
- service-zone and capacity references;
- evidence references;
- next question or next action;
- policy and compiler versions.

No model response alone changes qualification state.

## 4. Intake and marketing

The product must provide native, configurable intake and marketing capabilities:

- branded landing and intake forms;
- inbound email capture;
- inbound SMS capture;
- inbound phone-event capture;
- social lead-source ingestion;
- referral and partner-source attribution;
- campaign and creative attribution;
- configurable nurture journeys;
- contactability and consent management;
- approved email/SMS templates;
- campaign performance and transaction attribution.

All inbound sources produce the same canonical ingress envelope. Source-specific metadata is preserved as evidence and does not change canonical identity without deterministic resolution.

Marketing automation may choose among preconfigured actions and timing within immutable quiet-hour, frequency, consent, fair-housing, and authority constraints. It cannot publish or send through an unactivated connector.

## 5. Bulk ingestion

The product must accept CSV, XLSX, vCard, CRM exports, social-platform lead exports, contact exports, and supported marketing-platform exports.

The import operation is autonomous:

1. preserve the original artifact and digest;
2. detect file format and encoding;
3. infer columns and records;
4. map source fields to ontology fields;
5. classify rows as person, lead, buyer party, partner, or unresolved;
6. resolve duplicates against canonical identity;
7. create or supersede canonical records;
8. attach row-level provenance and source confidence;
9. create required consent/contactability states without inventing consent;
10. create follow-up journeys for eligible leads;
11. report only ambiguous or rejected rows.

The agent may infer a field mapping, but it must record the mapping, confidence, source column, and import policy version. Low-confidence mappings are quarantined for review; they are not silently written as facts.

Repeated imports are idempotent by tenant, source account, source artifact digest, and stable source-row identity. A changed row creates a new canonical version and preserves the prior record.

## 6. Provider connection experience

Provider connection is app-native.

The UI presents provider buttons, not credential forms. A connection flow must:

1. identify the provider and capability requested;
2. launch the provider's official application, browser login, OAuth, or supported local/CLI authorization flow;
3. return an authorization result or authenticated local bridge;
4. store only an opaque credential reference;
5. read back provider account, scopes, plan/entitlement, capabilities, and expiry;
6. create a signed capability inventory;
7. require release activation before any provider-changing effect.

The product must never request pasted API keys, passwords, session cookies, or copied credential material in an ordinary form.

Provider status:

- OpenAI/Codex subscription: ChatGPT sign-in through the supported Codex authorization flow.
- OpenAI self-hosted: local gpt-oss endpoint with operator-managed local authentication and model digest.
- Grok: subscription connection is supported only if xAI provides an authorized external-app or local-client path. xAI API access is a separate API credential and billing class.
- GLM: Coding Plan use is restricted to officially supported tools unless Z.AI authorizes this product in writing. General Z.AI API access is a separate credential and billing class.

If a provider cannot authorize the requested use, the connector state is unavailable and the system fails closed. It must not imitate subscription access or route through an unofficial proxy.

## 7. UX requirements

The default operator interaction is:

- one agent workspace;
- one buyer wizard;
- one inbox/exception stream;
- one import action;
- one provider-connection surface;
- progressive disclosure of detail;
- no required CRM data-entry screens for ordinary operation;
- clear next action and reason;
- reversible corrections through canonical commands;
- accessible web and iOS surfaces.

The system may expose advanced views for evidence, policy, provenance, and exceptions, but they are not required for routine buyer operations.

## 8. Acceptance

The implementation is not complete until tests and production evidence demonstrate:

- a new lead can enter through every enabled intake source;
- bulk files create correct canonical leads with provenance and duplicate resolution;
- the wizard identifies the next missing information deterministically;
- the agent qualifies leads without inventing facts or consent;
- qualification drives consultation readiness;
- consultation booking uses real provider snapshots and reconciles unknown outcomes;
- provider connections complete through supported vendor authorization flows;
- unsupported subscription paths fail closed with a clear explanation;
- routine operator work requires no CRM form entry;
- accessibility, provenance, idempotency, and audit evidence are bound to the exact release.