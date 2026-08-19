import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import { compareSchemas } from "./compatibility.ts";
import { registry, validateRecord } from "./registry.ts";
import { defaultSemanticPolicy, validateGatewayPair, validateSemantics } from "./semantic.ts";
import { admitOntologyRecord, validateOntologyRecord } from "./validate.ts";
import { ContractViolation } from "./violations.ts";
import { canonicalEnvelope } from "../canonical/envelope.ts";
import { assertNotModelFact, consultationReady, mayContact } from "../canonical/predicates.ts";

const dir = dirname(fileURLToPath(import.meta.url));

function load(name: string) {
  return JSON.parse(readFileSync(join(dir, "fixtures", name), "utf8")) as Record<string, unknown>;
}

const agreement = load("written_buyer_agreement.json");
const request = load("cognitive_work_request.json");
const proposal = load("cognitive_proposal.json");
const policy = defaultSemanticPolicy(new Date("2029-12-31T00:00:00Z"));

describe("PKT-00 hash-pinned contracts", () => {
  it("pins ontology and gateway digests", () => {
    assert.equal(
      registry.ontology.sha256,
      "cd6c8b12393e586919322e9b8e876eb05be2b7f1a3590dd6b014f2f44bf089bb",
    );
    assert.equal(
      registry.gateway.sha256,
      "52d80302f683fc7d15206e25819416542a3d4e56f8698392755eda431362b136",
    );
  });
});

describe("PKT-00 golden fixtures", () => {
  it("admits the governing written-agreement fixture", () => {
    admitOntologyRecord(agreement, new Date("2029-12-31T00:00:00Z"));
  });

  it("admits the gateway request and proposal fixtures", () => {
    validateRecord(request, "gateway");
    validateRecord(proposal, "gateway");
    validateSemantics(request, policy);
    validateSemantics(proposal, policy);
    validateGatewayPair(request, proposal, policy);
  });

  it("rejects an unknown schema version before mutation", () => {
    assert.throws(
      () => validateOntologyRecord({ ...agreement, schemaVersion: "buyer-ops/9.9.9" }),
      (err: unknown) =>
        err instanceof ContractViolation &&
        err.violations.some((v) => v.code === "UNSUPPORTED_SCHEMA_VERSION"),
    );
  });

  it("rejects unknown fields on a gateway request", () => {
    assert.throws(
      () => validateRecord({ ...request, modelMayWrite: true }, "gateway"),
      (err: unknown) =>
        err instanceof ContractViolation &&
        err.violations.some((v) => v.code === "STRUCTURAL_SCHEMA"),
    );
  });

  it("fails closed on prohibited + agent approval", () => {
    assert.throws(
      () =>
        validateRecord(
          { ...proposal, policyDisposition: "prohibited", requiredApproval: "agent" },
          "gateway",
        ),
      (err: unknown) =>
        err instanceof ContractViolation &&
        err.violations.some((v) => v.code === "STRUCTURAL_SCHEMA"),
    );
  });

  it("rejects a cross-work proposal pair", () => {
    assert.throws(
      () => validateGatewayPair(request, { ...proposal, workId: "other-work" }, policy),
      (err: unknown) =>
        err instanceof ContractViolation &&
        err.violations.some((v) => v.code === "REQUEST_PROPOSAL_MISMATCH"),
    );
  });

  it("rejects a showing-only term longer than 14 days", () => {
    assert.throws(
      () => validateSemantics({ ...agreement, terminatesAt: "2030-01-17T00:00:01Z" }),
      (err: unknown) =>
        err instanceof ContractViolation &&
        err.violations.some((v) => v.code === "NON_REP_TERM_EXCEEDED"),
    );
  });

  it("rejects showing-only advice services", () => {
    assert.throws(
      () =>
        validateSemantics({
          ...agreement,
          serviceDefinitions: [
            { serviceCode: "showing_access", allowed: true },
            { serviceCode: "property_advice", allowed: true },
          ],
        }),
      (err: unknown) =>
        err instanceof ContractViolation &&
        err.violations.some((v) => v.code === "NON_REP_SERVICE_SCOPE"),
    );
  });

  it("rejects unresolved proposal claims", () => {
    const mutated = structuredClone(proposal);
    (mutated.proposedActions as Array<{ sourceClaimIds: string[] }>)[0].sourceClaimIds = [
      "missing-claim",
    ];
    assert.throws(
      () => validateSemantics(mutated, policy),
      (err: unknown) =>
        err instanceof ContractViolation &&
        err.violations.some((v) => v.code === "UNRESOLVED_SOURCE_CLAIM"),
    );
  });
});

describe("PKT-00 compatibility", () => {
  it("detects required-field addition as breaking", () => {
    const previous = { type: "object", properties: { id: { type: "string" } } };
    const current = {
      type: "object",
      properties: { id: { type: "string" } },
      required: ["id"],
    };
    const findings = compareSchemas(previous, current);
    assert.deepEqual(
      findings.map((f) => [f.rule, f.breaking]),
      [["REQUIRED_ADDED", true]],
    );
  });

  it("allows optional property addition", () => {
    const previous = { type: "object", properties: { id: { type: "string" } } };
    const current = {
      type: "object",
      properties: { id: { type: "string" }, note: { type: "string" } },
    };
    assert.deepEqual(compareSchemas(previous, current), []);
  });
});

function personRecord() {
  const now = new Date("2030-01-01T00:00:00Z");
  return {
    ...canonicalEnvelope({
      id: "11111111-1111-4111-8111-111111111111",
      tenantId: "tenant-1",
      recordType: "Person",
      status: "active",
      createdBy: { actorType: "license_holder" as const, actorId: "agent-1" },
      sourceEvidenceIds: ["evidence-1"],
      now,
    }),
    identityState: "resolved",
    displayName: "Elena Vasquez",
    endpoints: [
      {
        endpointId: "ep-1",
        type: "email",
        normalizedValue: "elena.vasquez@example.com",
        verificationState: "provider_observed",
        status: "active",
      },
    ],
  };
}

describe("PKT-01 canonical admission", () => {
  it("admits a Person, BuyingParty, BuyerJourney, and proposed Appointment", () => {
    const now = new Date("2030-01-01T00:00:00Z");
    const person = personRecord();
    admitOntologyRecord(person, now);

    const party = {
      ...canonicalEnvelope({
        id: "22222222-2222-4222-8222-222222222222",
        tenantId: "tenant-1",
        recordType: "BuyingParty",
        status: "active",
        createdBy: { actorType: "license_holder" as const, actorId: "agent-1" },
        sourceEvidenceIds: ["evidence-1"],
        now,
      }),
      members: [{ personId: person.id, role: "buyer" }],
    };
    admitOntologyRecord(party, now);

    const journey = {
      ...canonicalEnvelope({
        id: "33333333-3333-4333-8333-333333333333",
        tenantId: "tenant-1",
        recordType: "BuyerJourney",
        status: "active",
        createdBy: { actorType: "license_holder" as const, actorId: "agent-1" },
        sourceEvidenceIds: ["evidence-1"],
        now,
      }),
      buyingPartyId: party.id,
      ownerLicenseHolderId: "agent-1",
      journeyState: "consultation_ready",
      qualificationState: "sufficient_for_consult",
      representationState: "not_represented",
    };
    admitOntologyRecord(journey, now);

    const appointment = {
      ...canonicalEnvelope({
        id: "44444444-4444-4444-8444-444444444444",
        tenantId: "tenant-1",
        recordType: "Appointment",
        status: "active",
        createdBy: { actorType: "license_holder" as const, actorId: "agent-1" },
        sourceEvidenceIds: ["evidence-1"],
        now,
      }),
      journeyId: journey.id,
      appointmentType: "buyer_consultation",
      participantIds: [person.id, "agent-1"],
      startsAt: "2030-01-03T16:00:00Z",
      endsAt: "2030-01-03T16:45:00Z",
      timeZone: "America/Chicago",
      locationOrMode: "proposed_local_policy_slot",
      appointmentState: "proposed",
    };
    admitOntologyRecord(appointment, now);
  });

  it("rejects an unknown field on a Person", () => {
    assert.throws(
      () => admitOntologyRecord({ ...personRecord(), fakePermit: true }),
      (err: unknown) =>
        err instanceof ContractViolation &&
        err.violations.some((v) => v.code === "STRUCTURAL_SCHEMA"),
    );
  });

  it("rejects model output written as VerifiedFact", () => {
    assert.throws(
      () =>
        assertNotModelFact({
          epistemicType: "verified_fact",
          speakerOrMethodRef: "model:grok",
        }),
      (err: unknown) =>
        err instanceof ContractViolation &&
        err.violations.some((v) => v.code === "ILLEGAL_MODEL_FACT"),
    );
  });
});

describe("PKT-01 predicates", () => {
  it("lets suppression defeat contact", () => {
    const person = personRecord();
    const result = mayContact({
      person: person as never,
      consents: [
        {
          ...person,
          recordType: "ConsentGrant",
          personId: person.id,
          channel: "form",
          purpose: "transactional_inquiry",
          validityState: "active",
        } as never,
      ],
      suppressions: [
        {
          ...person,
          recordType: "Suppression",
          subjectId: person.id,
          validityState: "active",
        } as never,
      ],
      channel: "email",
      purpose: "transactional_inquiry",
    });
    assert.equal(result.allowed, false);
    assert.ok(result.reasons.includes("suppressed"));
  });

  it("requires the four consult criteria", () => {
    const person = personRecord();
    const missing = consultationReady({
      person: person as never,
      journey: {
        ...person,
        recordType: "BuyerJourney",
        buyingPartyId: "party-1",
        ownerLicenseHolderId: "agent-1",
        journeyState: "qualifying",
        qualificationState: "collecting",
        representationState: "not_represented",
      } as never,
      observations: [],
      openIdentityCase: false,
    });
    assert.equal(missing.ready, false);
    assert.ok(missing.reasons.includes("missing_purchase_intent"));
    assert.ok(missing.reasons.includes("missing_budget_financing"));
  });
});
