export type Violation = {
  code: string;
  path: string;
  message: string;
};

export class ContractViolation extends Error {
  readonly violations: readonly Violation[];

  constructor(violations: Violation[]) {
    super(violations.map((v) => `${v.code} at ${v.path}: ${v.message}`).join("; "));
    this.name = "ContractViolation";
    this.violations = violations;
  }
}

export class ActivationBlocked extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(`${code}: ${message}`);
    this.name = "ActivationBlocked";
    this.code = code;
  }
}

export const SCHEMA_VERSION = "buyer-ops/0.1.0";
export const ONTOLOGY_RECORD_TYPES = [
  "Person",
  "BuyingParty",
  "BuyerJourney",
  "ConsentGrant",
  "Suppression",
  "QualificationObservation",
  "Appointment",
  "Commitment",
  "PropertyReference",
  "IabsDelivery",
  "WrittenBuyerAgreement",
  "AgreementQualification",
  "RepresentationRelationship",
  "EpistemicItem",
  "EffectAttempt",
] as const;

export type OntologyRecordType = (typeof ONTOLOGY_RECORD_TYPES)[number];
