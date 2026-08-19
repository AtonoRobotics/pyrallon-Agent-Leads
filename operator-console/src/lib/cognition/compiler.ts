import type { Sql } from "@/lib/db";
import { ActivationBlocked, ContractViolation } from "@/lib/contracts/violations.ts";
import { iso, newId, sha256Digest } from "@/lib/canonical/envelope.ts";
import { getRecord, insertCanonical, listByField, type StoredRecord } from "@/lib/canonical/store.ts";
import type { JourneyRecord, ObservationRecord, PersonRecord } from "@/lib/canonical/predicates.ts";
import { assertAllowedCriterion, assertNoProtectedInfluence } from "@/lib/fair-housing/features.ts";
import { validateGatewayRecord } from "@/lib/contracts/validate.ts";
import { validateGatewayPair, validateSemantics } from "@/lib/contracts/semantic.ts";
import { canonicalEnvelope } from "@/lib/canonical/envelope.ts";
import { logEvent } from "@/lib/observability/log.ts";

export const ALLOWED_PROPOSAL_ACTIONS = new Set([
  "lead_qualification_draft",
  "answer_from_approved_knowledge",
  "ask_qualification_question",
  "acknowledge_buyer_statement",
  "propose_qualification_observation",
  "identify_contradiction_or_unknown",
  "propose_consultation_readiness",
  "escalate_exception",
]);

const PROHIBITED_PROPOSAL_ACTIONS = new Set([
  "outbound_email",
  "outbound_sms",
  "outbound_acknowledgment",
  "calendar_write",
  "outbound_ai_voice",
  "send_message",
  "provider_write",
  "verified_fact",
]);

type CompiledContext = {
  manifestId: string;
  packet: Record<string, unknown>;
  digest: string;
  personId: string;
  journeyId: string;
  sourceRecordIds: string[];
};

export async function compileContext(
  sql: Sql,
  input: { tenantId: string; journeyId: string; now?: Date },
): Promise<CompiledContext> {
  const journey = await getRecord<JourneyRecord>(sql, input.tenantId, input.journeyId);
  if (!journey) throw new Error("Journey not found");
  const party = await getRecord<StoredRecord & { members: Array<{ personId: string }> }>(
    sql,
    input.tenantId,
    journey.buyingPartyId,
  );
  const person = party
    ? await getRecord<PersonRecord>(sql, input.tenantId, party.members[0]?.personId ?? "")
    : null;
  if (!person) throw new Error("Person not found");
  const observations = await listByField<ObservationRecord>(
    sql,
    input.tenantId,
    "QualificationObservation",
    "journeyId",
    input.journeyId,
  );
  const now = input.now ?? new Date();
  const facts = {
    ontologyVersion: "buyer-ops/0.1.0",
    journeyId: journey.id,
    journeyVersion: journey.version,
    personId: person.id,
    identityState: person.identityState,
    representationState: journey.representationState,
    observations: observations.map((o) => ({
      criterionId: o.criterionId,
      observationState: o.observationState,
    })),
  };
  const content = JSON.stringify(facts);
  const compiledAt = iso(now);
  const expiresAt = new Date(now.getTime() + 10 * 60_000).toISOString();
  const manifestId = newId();
  const sourceRecordIds = [journey.id, person.id, ...observations.map((o) => o.id)];
  const packet = {
    schemaVersion: "context-packet/1.0.0",
    manifestId,
    ontologyVersion: "buyer-ops/0.1.0",
    compiledAt,
    expiresAt,
    sections: [
      {
        sectionId: "facts",
        purpose: "Ground qualification draft",
        contentDigest: sha256Digest(content),
        content: facts,
        sourceRecordIds,
      },
    ],
  };
  return {
    manifestId,
    packet,
    digest: sha256Digest(JSON.stringify(packet)),
    personId: person.id,
    journeyId: journey.id,
    sourceRecordIds,
  };
}

export function compileWorkRequest(input: {
  tenantId: string;
  principalId: string;
  journeyId: string;
  compiled: CompiledContext;
  now?: Date;
}) {
  const now = input.now ?? new Date();
  return {
    schemaVersion: "cognitive-work/1.1.0",
    recordType: "CognitiveWorkRequest",
    workId: newId(),
    tenantId: input.tenantId,
    principalId: input.principalId,
    buyerJourneyId: input.journeyId,
    workflowId: `buyer-journey:${input.journeyId}`,
    actionClass: "lead_qualification_draft",
    objective: "Prepare a grounded qualification response",
    contextManifestId: input.compiled.manifestId,
    contextPacket: input.compiled.packet,
    contextSufficiencyContractVersion: "lead-qualification/1.0.0",
    requiredProposalSchemaVersion: "cognitive-proposal/1.1.0",
    routePolicyVersion: "route/deterministic-local/1.0.0",
    retryBudget: { maxAttempts: 1, maxElapsedMs: 5000 },
    degradationPolicyVersion: "degradation/fail-closed/1.0.0",
    traceId: newId(),
    deadline: new Date(now.getTime() + 5 * 60_000).toISOString(),
  };
}

export function deterministicQualificationProposal(input: {
  workId: string;
  contextManifestId: string;
  personId: string;
  journeyId: string;
  message: string;
  sourceIds: string[];
  now?: Date;
}) {
  assertNoProtectedInfluence(input.message);
  const now = input.now ?? new Date();
  const startedAt = iso(now);
  const completedAt = iso(now);
  const claims: Array<Record<string, unknown>> = [];
  const actions: Array<Record<string, unknown>> = [];
  const unknowns: Array<Record<string, unknown>> = [];
  const lower = input.message.toLowerCase();
  const detections: Array<[string, string]> = [];
  if (/(buy|buying|purchase|relocating|moving)/.test(lower)) {
    detections.push(["purchase_intent", "Buyer stated purchase or relocation intent"]);
  }
  if (/(austin|san antonio|fredericksburg|alamo|houston|dallas|mueller)/.test(lower)) {
    detections.push(["geography", "Buyer stated a geography"]);
  }
  if (/(week|month|fall|spring|soon|immediately|\d{4}|thursday|friday)/.test(lower)) {
    detections.push(["timing", "Buyer stated timing"]);
  }
  if (/(\$|k\b|budget|financing|pre-?approv|cash)/.test(lower)) {
    detections.push(["budget_financing", "Buyer stated budget or financing"]);
  }
  const sourceIds = input.sourceIds.length ? input.sourceIds : ["inbound:body"];
  const expiresAt = new Date(now.getTime() + 5 * 60_000).toISOString();
  for (const [criterion, value] of detections) {
    assertAllowedCriterion(criterion);
    const claimId = `claim-${criterion}`;
    claims.push({
      claimId,
      subjectRef: input.personId,
      predicate: criterion,
      value,
      epistemicType: "assertion",
      sourceIds,
      freshnessAt: startedAt,
    });
    actions.push({
      proposalId: `act-${criterion}`,
      actionClass: "propose_qualification_observation",
      targetRefs: [input.journeyId],
      recipientRefs: [input.personId],
      normalizedPayload: { criterionId: criterion, value, epistemicType: "assertion" },
      sourceClaimIds: [claimId],
      requestedExecutionWindow: { notBefore: startedAt, expiresAt },
      idempotencySeed: `${input.workId}:act-${criterion}`,
    });
  }
  if (!actions.length) {
    claims.push({
      claimId: "claim-unknown",
      subjectRef: input.personId,
      predicate: "qualification",
      value: "No extractable qualification criteria",
      epistemicType: "assertion",
      sourceIds,
      freshnessAt: startedAt,
    });
    unknowns.push({
      unknownId: "qualification-criteria",
      description: "Inbound text did not yield an allowlisted criterion",
      blocking: false,
      permittedResolution: "ask_qualification_question",
    });
    actions.push({
      proposalId: "act-unknown",
      actionClass: "identify_contradiction_or_unknown",
      targetRefs: [input.journeyId],
      recipientRefs: [input.personId],
      normalizedPayload: { criterionId: "identity", value: "unknown" },
      sourceClaimIds: ["claim-unknown"],
      requestedExecutionWindow: { notBefore: startedAt, expiresAt },
      idempotencySeed: `${input.workId}:act-unknown`,
    });
  }
  assertAllowedActions(actions);
  const proposal = {
    schemaVersion: "cognitive-proposal/1.1.0",
    recordType: "CognitiveProposal",
    workId: input.workId,
    actionClass: "lead_qualification_draft",
    proposedActions: actions,
    claims,
    unknowns,
    assumptions: ["Deterministic extractor; claims remain assertions."],
    risks: ["Live model route is not activated."],
    confidence: detections.length ? 0.72 : 0.2,
    requiredApproval: "none",
    policyDisposition: "eligible",
    proposedAt: startedAt,
    expiresAt,
    contextManifestId: input.contextManifestId,
    runtimeEvidence: {
      invocationId: newId(),
      attempt: 1,
      routePolicyVersion: "route/deterministic-local/1.0.0",
      routeId: "deterministic-qualification",
      providerId: "deterministic-local",
      adapterId: "ot01-qualification",
      adapterVersion: "1.0.0",
      transport: "process",
      credentialIdentityRef: "local-compiler",
      authClass: "local_endpoint",
      billingClass: "internal",
      modelFamily: "deterministic-rules",
      resolvedModelId: "pkt08-qualification-v1",
      capabilityProfileVersion: "capability/deterministic/1.0.0",
      evaluationQualificationId: "eval-deterministic",
      startedAt,
      completedAt,
      usage: { inputUnits: input.message.length, outputUnits: claims.length, unitType: "unknown" },
    },
  };
  return proposal;
}

export function assertAllowedActions(actions: Array<Record<string, unknown>>) {
  for (const action of actions) {
    const name = String(action.actionClass ?? action.action ?? "");
    if (PROHIBITED_PROPOSAL_ACTIONS.has(name) || !ALLOWED_PROPOSAL_ACTIONS.has(name)) {
      throw new ContractViolation([
        {
          code: "PROHIBITED_COGNITIVE_ACTION",
          path: "$.proposedActions",
          message: name,
        },
      ]);
    }
  }
}

export function assertLiveCognitionActivated() {
  if (!process.env.XAI_API_KEY) {
    throw new ActivationBlocked(
      "COGNITION_NOT_ACTIVATED",
      "Live cognition requires an authorized route and GATE-013 evidence. Deterministic qualification is available.",
    );
  }
}

export function validateProposal(proposal: Record<string, unknown>) {
  validateGatewayRecord(proposal);
  validateSemantics(proposal);
  const actions = Array.isArray(proposal.proposedActions)
    ? (proposal.proposedActions as Array<Record<string, unknown>>)
    : [];
  assertAllowedActions(actions);
  const claims = Array.isArray(proposal.claims) ? (proposal.claims as Array<Record<string, unknown>>) : [];
  for (const claim of claims) {
    if (claim.epistemicType === "verified_fact") {
      throw new ContractViolation([
        {
          code: "ILLEGAL_MODEL_FACT",
          path: "$.claims",
          message: "a proposal cannot introduce verified_fact",
        },
      ]);
    }
  }
}

export function validateWorkPair(request: Record<string, unknown>, proposal: Record<string, unknown>) {
  validateGatewayRecord(request);
  validateProposal(proposal);
  validateGatewayPair(request, proposal);
}

export async function applyProposal(
  sql: Sql,
  input: {
    tenantId: string;
    licenseHolderId: string;
    journeyId: string;
    personId: string;
    proposal: Record<string, unknown>;
    now?: Date;
  },
) {
  validateProposal(input.proposal);
  const now = input.now ?? new Date();
  const createdBy = { actorType: "license_holder" as const, actorId: input.licenseHolderId };
  const actions = (input.proposal.proposedActions as Array<Record<string, unknown>>) ?? [];
  const claims = (input.proposal.claims as Array<Record<string, unknown>>) ?? [];
  const written: string[] = [];
  for (const action of actions) {
    if (action.actionClass !== "propose_qualification_observation") continue;
    const payload = (action.normalizedPayload ?? {}) as { criterionId?: string; value?: string };
    const criterion = String(payload.criterionId ?? "");
    const value = String(payload.value ?? "");
    assertAllowedCriterion(criterion);
    const claim = claims.find((c) =>
      Array.isArray(action.sourceClaimIds) && action.sourceClaimIds.includes(c.claimId),
    );
    if (claim?.epistemicType === "verified_fact") {
      throw new ContractViolation([
        {
          code: "ILLEGAL_MODEL_FACT",
          path: "$.claims",
          message: "cannot apply verified_fact from a proposal",
        },
      ]);
    }
    const existing = await listByField<ObservationRecord>(
      sql,
      input.tenantId,
      "QualificationObservation",
      "journeyId",
      input.journeyId,
    );
    if (existing.some((o) => o.criterionId === criterion)) continue;
    const itemId = newId();
    await insertCanonical(
      sql,
      {
        ...canonicalEnvelope({
          id: itemId,
          tenantId: input.tenantId,
          recordType: "EpistemicItem",
          status: "current",
          createdBy,
          sourceEvidenceIds: [itemId],
          now,
        }),
        epistemicType: "assertion",
        proposition: {
          subjectRef: input.personId,
          predicate: criterion,
          value,
          applicableJourneyId: input.journeyId,
          validFrom: iso(now),
        },
        speakerOrMethodRef: input.personId,
        validityState: "current",
      },
      now,
    );
    await insertCanonical(
      sql,
      {
        ...canonicalEnvelope({
          tenantId: input.tenantId,
          recordType: "QualificationObservation",
          status: "current",
          createdBy,
          sourceEvidenceIds: [itemId],
          now,
        }),
        journeyId: input.journeyId,
        criterionId: criterion,
        epistemicItemId: itemId,
        observationState: "asserted",
      },
      now,
    );
    written.push(criterion);
  }
  await logEvent(sql, {
    tenantId: input.tenantId,
    kind: "proposal_applied",
    journeyId: input.journeyId,
    payload: { written, workId: input.proposal.workId },
    now,
  });
  return { written };
}
