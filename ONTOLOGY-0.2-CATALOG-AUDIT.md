# Ontology 0.2 Catalog-to-Schema Audit

**Target version:** `buyer-ops/0.2.0`  
**PKT-00 status:** Reopened  
**PKT-01 and PKT-02 status:** Prior work provisionally valid pending 0.2.0 compatibility  
**PKT-03 status:** Blocked

## Admission rule

Every named canonical type in §3 of `ONTOLOGY-V0-CONTRACT.md` must be selectable at the root of `ONTOLOGY-V0.schema.json`. A reusable value object is not a canonical type. `EpistemicItem` is an abstract relationship target covering the five distinct epistemic records; it is not a root record in 0.2.0.

## Audit result

The 0.1.0 schema admitted 14 catalog records plus the non-catalog `EpistemicItem` compatibility shape. It omitted 24 catalog records. The 0.2.0 revision admits all 38 catalog records and replaces the abstract `EpistemicItem` storage shape with the five catalog-defined epistemic records.

| Catalog type | 0.1.0 root | 0.2.0 disposition |
|---|---:|---|
| Tenant | No | Add canonical record |
| Brokerage | No | Add canonical record |
| LicenseHolder | No | Add canonical record |
| ServicePrincipal | No | Add canonical record |
| Person | Yes | Retain; endpoints become canonical references |
| ContactEndpoint | No; value object only | Add canonical record |
| BuyingParty | Yes | Retain; make decision authority explicit |
| BuyerJourney | Yes | Retain; add territory and lead-source references |
| Conversation | No | Add canonical record |
| Message | No | Add canonical record |
| ConsentGrant | Yes | Retain |
| Suppression | Yes | Retain |
| LeadSource | No | Add canonical record |
| QualificationCriterion | No | Add canonical record |
| QualificationObservation | Yes | Retain; reference a concrete epistemic record |
| BuyerRequirement | No | Add canonical record |
| FinancingReadiness | No | Add canonical record |
| Appointment | Yes | Retain |
| Commitment | Yes | Retain |
| PropertyReference | Yes | Retain |
| DocumentArtifact | No | Add canonical record |
| IabsDelivery | Yes | Retain |
| WrittenBuyerAgreement | Yes | Retain |
| AgreementQualification | Yes | Retain |
| RepresentationRelationship | Yes | Retain |
| Transaction | No | Add canonical record |
| TransactionMilestone | No | Add canonical record |
| Authorization | No | Add canonical record |
| Approval | No | Add canonical record |
| EffectAttempt | Yes | Retain |
| Evidence | No; collapsed into EpistemicItem | Add distinct canonical record |
| Assertion | No; collapsed into EpistemicItem | Add distinct canonical record |
| VerifiedFact | No; collapsed into EpistemicItem | Add distinct canonical record |
| Inference | No; collapsed into EpistemicItem | Add distinct canonical record |
| Memory | No; collapsed into EpistemicItem | Add distinct canonical record |
| Contradiction | No | Add canonical record |
| Correction | No | Add canonical record |
| WorkflowReference | No | Add canonical record |

## Compatibility classification

Adding the omitted catalog records is a minor semantic-version change. Existing 0.1.0 rows require an explicit envelope migration to 0.2.0. Legacy `EpistemicItem` rows require a discriminator-based conversion to one of the five concrete epistemic records; conversion must fail closed if the old row lacks fields required by that concrete type. No row is silently reinterpreted.

