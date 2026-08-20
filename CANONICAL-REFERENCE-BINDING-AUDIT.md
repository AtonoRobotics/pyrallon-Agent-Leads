# Canonical Reference-Binding Audit

**Ontology:** `buyer-ops/0.3.0`
**Audit date:** 2026-08-19
**Status:** non-governing audit and recommendations only. This document neither adds admission
rules nor changes the meaning of ontology records.

**Disposition:** executable mappings are limited to bindings already stated by the schema's typed
discriminators, type-named fields, or `ONTOLOGY-V0-CONTRACT.md` §4. The unresolved bindings below
are reported for specification-owner review; the implementation does not create categorical
rejection codes or owner-verifier interfaces for them.

## Admitted binding sources

| Binding class | Executable examples | Governing source |
|---|---|---|
| Typed discriminator | Conversation participants, Message sender/recipients, ContactEndpoint owner, Authorization grantor/grantee, ConnectorGrant principal/grantor, ConfirmedTransactionDate source | Ontology 0.3.0 enum plus paired `*Id` |
| Type-named field | `personId`, `brokerageId`, `journeyId`, `agreementId`, `evidenceId`, `artifactId`, and their plural forms | Ontology 0.3.0 field name and corresponding executable root type |
| Declared abstract union | Epistemic inputs and sources | Ontology contract §5 defines `EpistemicItem` as Evidence, Assertion, VerifiedFact, Inference, or Memory |
| Declared relationship | Buying-party membership, journey ownership, conversation participation, endpoint ownership, consent, suppression, requirements, appointments, commitments, IABS, agreements, representation, properties, support, contradiction, supersession | Ontology contract §4 |

All admitted canonical targets must resolve in the record's tenant. Missing, cross-tenant, and
wrong-type targets are rejected. Provider IDs, storage references, workflow engine IDs, policy IDs,
credential bindings, connector IDs, and external thread/message/receipt identifiers are not treated
as canonical records merely because they use the shared lexical `Id` definition.

## Undefined bindings

| Record and field | Missing governing information | Recommendation |
|---|---|---|
| `Appointment.participantIds` | Participant type/discriminator and allowed union | Publish the target union and fixtures |
| `Transaction.partyIds` | Party type/discriminator and allowed union | Publish the target union and fixtures |
| `Commitment.obligorId` | Definition of the contract's `Actor` abstraction | Publish the actor abstraction |
| `FinancingReadiness.verificationSourceIds` | Canonical versus external source domain and allowed union | Publish source identity rules |
| `ConsentGrant.principalId` | Principal kind and target domain | Publish principal kinds and targets |
| `ConsentGrant.presentationEvidenceId` | Formal canonical/external evidence binding | Publish evidence identity rules |
| `Approval.approverId` when `approverType=brokerage_reviewer` | Canonical type or governed reviewer-identity registry | Publish reviewer identity rules |
| `Authorization.resourceType/resourceId` | Closed resource vocabulary and target-domain mapping | Publish the resource vocabulary |
| `WorkflowReference.subjectType/subjectId` | Closed subject vocabulary and target-domain mapping | Publish the subject vocabulary |

These are possible specification gaps, not implementation extension points. No new mapping should
be enforced until the governing schema or contract publishes the vocabulary, cardinality, temporal
behavior, error behavior, and migration compatibility.

The catalog also says `SUPERSEDES` preserves the same semantic subject but does not publish
record-specific subject identity predicates. The owner should publish those predicates. This audit
does not prescribe an injected verifier or a new rejection behavior.

## Required owner publication

For every undefined binding, publish the closed discriminator values, exact target type for each
value, whether the identifier may be external, tenant-boundary rule, required current/temporal
state, cardinality, supersession behavior, and valid/invalid cross-record fixtures. Regenerate the
Pydantic models and rerun contract synchronization, migration, canonical admission, and PostgreSQL
transactional tests after publication.
