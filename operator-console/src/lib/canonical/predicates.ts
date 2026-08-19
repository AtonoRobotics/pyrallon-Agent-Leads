import type { StoredRecord } from "./store.ts";
import { ContractViolation } from "../contracts/violations.ts";

export type PersonRecord = StoredRecord & {
  identityState: string;
  displayName: string;
  endpoints: Array<{
    endpointId: string;
    type: string;
    normalizedValue: string;
    verificationState: string;
    status: string;
  }>;
};

export type JourneyRecord = StoredRecord & {
  buyingPartyId: string;
  ownerLicenseHolderId: string;
  journeyState: string;
  qualificationState: string;
  representationState: string;
};

export type ConsentRecord = StoredRecord & {
  personId: string;
  channel: string;
  purpose: string;
  validityState: string;
  basis?: string;
};

export type SuppressionRecord = StoredRecord & {
  subjectId: string;
  validityState: string;
};

export type ObservationRecord = StoredRecord & {
  journeyId: string;
  criterionId: string;
  epistemicItemId: string;
  observationState: string;
};

export type EpistemicRecord = StoredRecord & {
  epistemicType: string;
  proposition: { predicate: string; value: unknown };
  validityState: string;
  speakerOrMethodRef?: string;
};

const REQUIRED_FOR_CONSULT = [
  "purchase_intent",
  "geography",
  "timing",
  "budget_financing",
] as const;

export function mayContact(input: {
  person: PersonRecord;
  consents: ConsentRecord[];
  suppressions: SuppressionRecord[];
  channel: string;
  purpose: string;
}): { allowed: boolean; reasons: string[] } {
  const reasons: string[] = [];
  if (input.person.identityState === "conflict") reasons.push("identity_conflict");
  const suppressed = input.suppressions.some((s) => s.validityState === "active");
  if (suppressed) reasons.push("suppressed");
  const consent = input.consents.some(
    (c) =>
      c.validityState === "active" &&
      (c.channel === input.channel || c.channel === "form") &&
      c.purpose === input.purpose,
  );
  if (!consent) reasons.push("no_active_consent");
  const endpointOk = input.person.endpoints.some(
    (e) => e.type === input.channel && e.status !== "invalid" && e.status !== "suppressed",
  );
  if (input.channel !== "form" && !endpointOk) reasons.push("endpoint_not_contactable");
  return { allowed: reasons.length === 0, reasons };
}

export function consultationReady(input: {
  person: PersonRecord;
  journey: JourneyRecord;
  observations: ObservationRecord[];
  openIdentityCase: boolean;
}): { ready: boolean; reasons: string[] } {
  const reasons: string[] = [];
  if (input.person.identityState === "ambiguous" || input.openIdentityCase) {
    reasons.push("identity_unresolved");
  }
  if (input.person.identityState === "conflict") reasons.push("identity_conflict");
  if (input.journey.representationState === "conflict") reasons.push("representation_conflict");
  if (input.journey.journeyState === "suppressed") reasons.push("suppressed");
  if (input.observations.some((o) => o.observationState === "contradicted")) {
    reasons.push("qualification_contradicted");
  }
  for (const criterion of REQUIRED_FOR_CONSULT) {
    const hit = input.observations.find(
      (o) =>
        o.criterionId === criterion &&
        ["asserted", "verified", "inferred"].includes(o.observationState),
    );
    if (!hit) reasons.push(`missing_${criterion}`);
  }
  return { ready: reasons.length === 0, reasons };
}

export function assertNotModelFact(item: Pick<EpistemicRecord, "epistemicType" | "speakerOrMethodRef">) {
  const speaker = item.speakerOrMethodRef ?? "";
  if (item.epistemicType === "verified_fact" && speaker.startsWith("model:")) {
    throw new ContractViolation([
      {
        code: "ILLEGAL_MODEL_FACT",
        path: "$.epistemicType",
        message: "model output cannot become VerifiedFact",
      },
    ]);
  }
}
