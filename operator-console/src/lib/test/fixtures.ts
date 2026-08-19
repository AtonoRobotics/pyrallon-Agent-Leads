import type { Sql } from "../db.ts";
import { canonicalEnvelope, iso, newId, sha256Digest } from "../canonical/envelope.ts";
import { insertCanonical } from "../canonical/store.ts";
import { ensureDefaultGrants } from "../connectors/gateway.ts";
import { startJourneyWorkflow } from "../workflow/runtime.ts";
import type { PersonRecord } from "../canonical/predicates.ts";

export const NOW = new Date("2030-01-10T16:00:00Z");

export async function createTenant(sql: Sql, tenantId: string) {
  const licenseHolderId = newId();
  const brokerageId = newId();
  await sql`
    insert into tenant_profiles (
      tenant_id, brokerage_name, agent_name, license_number, license_holder_id, brokerage_id, seeded_at
    ) values (
      ${tenantId}, ${"Atono Brokerage"}, ${"Test Agent"}, ${"TX-SA-44821"},
      ${licenseHolderId}, ${brokerageId}, ${iso(NOW)}
    )
  `;
  await ensureDefaultGrants(sql, tenantId);
  return { tenantId, licenseHolderId, brokerageId, actor: { actorType: "license_holder" as const, actorId: licenseHolderId } };
}

export async function createBuyer(
  sql: Sql,
  input: {
    tenantId: string;
    licenseHolderId: string;
    name: string;
    email?: string;
    phone?: string;
    zone?: string;
    consultReady?: boolean;
    stop?: boolean;
    now?: Date;
  },
) {
  const now = input.now ?? NOW;
  const createdBy = { actorType: "license_holder" as const, actorId: input.licenseHolderId };
  const evId = newId();
  await insertCanonical(
    sql,
    {
      ...canonicalEnvelope({
        id: evId,
        tenantId: input.tenantId,
        recordType: "EpistemicItem",
        status: "current",
        createdBy,
        sourceEvidenceIds: [evId],
        now,
      }),
      epistemicType: "evidence",
      proposition: {
        subjectRef: input.tenantId,
        predicate: "source_artifact",
        value: `Inbound form for ${input.name}`,
        validFrom: iso(now),
      },
      validityState: "current",
    },
    now,
  );

  const endpoints = [];
  if (input.email) {
    endpoints.push({
      endpointId: newId(),
      type: "email" as const,
      normalizedValue: input.email.toLowerCase(),
      verificationState: "provider_observed" as const,
      status: input.stop ? ("suppressed" as const) : ("active" as const),
    });
  }
  if (input.phone) {
    endpoints.push({
      endpointId: newId(),
      type: "phone" as const,
      normalizedValue: input.phone,
      verificationState: "provider_observed" as const,
      status: input.stop ? ("suppressed" as const) : ("active" as const),
    });
  }

  const person = (await insertCanonical(
    sql,
    {
      ...canonicalEnvelope({
        tenantId: input.tenantId,
        recordType: "Person",
        status: "active",
        createdBy,
        sourceEvidenceIds: [evId],
        now,
      }),
      identityState: "resolved",
      displayName: input.name,
      endpoints,
    },
    now,
  )) as PersonRecord;

  for (const endpoint of person.endpoints) {
    await sql`
      insert into endpoint_index (tenant_id, endpoint_type, normalized_value, person_id, created_at)
      values (${input.tenantId}, ${endpoint.type}, ${endpoint.normalizedValue}, ${person.id}, ${iso(now)})
    `;
  }

  const party = await insertCanonical(
    sql,
    {
      ...canonicalEnvelope({
        tenantId: input.tenantId,
        recordType: "BuyingParty",
        status: "active",
        createdBy,
        sourceEvidenceIds: [evId],
        now,
      }),
      members: [{ personId: person.id, role: "buyer" }],
    },
    now,
  );

  const journey = await insertCanonical(
    sql,
    {
      ...canonicalEnvelope({
        tenantId: input.tenantId,
        recordType: "BuyerJourney",
        status: "active",
        createdBy,
        sourceEvidenceIds: [evId],
        now,
      }),
      buyingPartyId: party.id,
      ownerLicenseHolderId: input.licenseHolderId,
      journeyState: input.stop ? "suppressed" : input.consultReady ? "consultation_ready" : "captured",
      qualificationState: input.consultReady ? "sufficient_for_consult" : "collecting",
      representationState: "not_represented",
    },
    now,
  );

  await sql`
    insert into journey_ops (
      tenant_id, journey_id, source_channel, source_detail, service_zone,
      contactability, acknowledgment_state, consultation_state, nurture_state, blocker_codes
    ) values (
      ${input.tenantId}, ${journey.id}, ${"form"}, ${"test"}, ${input.zone ?? "San Antonio"},
      ${input.stop ? "suppressed" : "contactable"}, ${"not_sent"},
      ${input.consultReady ? "ready" : "not_ready"}, ${"inactive"},
      ${JSON.stringify(input.stop ? ["suppressed"] : [])}
    )
  `;

  if (input.stop) {
    await insertCanonical(
      sql,
      {
        ...canonicalEnvelope({
          tenantId: input.tenantId,
          recordType: "Suppression",
          status: "active",
          createdBy,
          sourceEvidenceIds: [evId],
          now,
        }),
        subjectId: person.id,
        scope: "all_non_required_contact",
        reason: "opt_out",
        suppressedAt: iso(now),
        validityState: "active",
      },
      now,
    );
  } else {
    await insertCanonical(
      sql,
      {
        ...canonicalEnvelope({
          tenantId: input.tenantId,
          recordType: "ConsentGrant",
          status: "active",
          createdBy,
          sourceEvidenceIds: [evId],
          now,
        }),
        personId: person.id,
        channel: "form",
        purpose: "transactional_inquiry",
        principalId: input.licenseHolderId,
        basis: "affirmative",
        grantedAt: iso(now),
        presentationEvidenceId: evId,
        validityState: "active",
      },
      now,
    );
  }

  if (input.consultReady) {
    for (const [criterion, value] of [
      ["purchase_intent", "Relocating this fall"],
      ["geography", "San Antonio"],
      ["timing", "This fall"],
      ["budget_financing", "Around 850000"],
    ] as const) {
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
            sourceEvidenceIds: [evId],
            now,
          }),
          epistemicType: "assertion",
          proposition: {
            subjectRef: person.id,
            predicate: criterion,
            value,
            applicableJourneyId: journey.id,
            validFrom: iso(now),
          },
          speakerOrMethodRef: person.id,
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
            sourceEvidenceIds: [evId],
            now,
          }),
          journeyId: journey.id,
          criterionId: criterion,
          epistemicItemId: itemId,
          observationState: "asserted",
        },
        now,
      );
    }
  }

  await startJourneyWorkflow(sql, { tenantId: input.tenantId, journeyId: String(journey.id), now });
  return { person, party, journey, evidenceId: evId };
}

export async function createProperty(
  sql: Sql,
  input: { tenantId: string; licenseHolderId: string; address: string; now?: Date },
) {
  const now = input.now ?? NOW;
  return insertCanonical(
    sql,
    {
      ...canonicalEnvelope({
        tenantId: input.tenantId,
        recordType: "PropertyReference",
        status: "current",
        createdBy: { actorType: "license_holder", actorId: input.licenseHolderId },
        sourceEvidenceIds: ["property-evidence"],
        now,
      }),
      addressText: input.address,
      suppliedBy: { actorType: "license_holder", actorId: input.licenseHolderId },
      suppliedAt: iso(now),
      sourceType: "agent",
    },
    now,
  );
}

export async function createIabs(
  sql: Sql,
  input: {
    tenantId: string;
    licenseHolderId: string;
    brokerageId: string;
    personId: string;
    now?: Date;
  },
) {
  const now = input.now ?? NOW;
  return insertCanonical(
    sql,
    {
      ...canonicalEnvelope({
        tenantId: input.tenantId,
        recordType: "IabsDelivery",
        status: "current",
        createdBy: { actorType: "license_holder", actorId: input.licenseHolderId },
        sourceEvidenceIds: ["iabs-artifact"],
        now,
      }),
      formId: "iabs-form",
      formVersion: "iabs-2024",
      jurisdiction: "TX",
      responsibleLicenseHolderId: input.licenseHolderId,
      brokerageId: input.brokerageId,
      recipientPersonId: input.personId,
      deliveryChannel: "in_person",
      deliveredAt: iso(now),
      trigger: "showing_prerequisite",
      artifactId: "iabs-artifact",
      artifactDigest: sha256Digest("iabs-bytes"),
      evidenceIds: ["iabs-artifact"],
      validityState: "delivered",
    },
    now,
  );
}

export async function createShowingAgreement(
  sql: Sql,
  input: {
    tenantId: string;
    licenseHolderId: string;
    brokerageId: string;
    buyerPartyId: string;
    now?: Date;
  },
) {
  const now = input.now ?? NOW;
  return insertCanonical(
    sql,
    {
      ...canonicalEnvelope({
        tenantId: input.tenantId,
        recordType: "WrittenBuyerAgreement",
        status: "effective",
        createdBy: { actorType: "license_holder", actorId: input.licenseHolderId },
        sourceEvidenceIds: ["agreement-artifact"],
        now,
      }),
      agreementType: "non_representation_showing",
      jurisdiction: "TX",
      brokerPartyId: input.brokerageId,
      responsibleLicenseHolderId: input.licenseHolderId,
      buyerPartyIds: [input.buyerPartyId],
      serviceDefinitions: [{ serviceCode: "showing_access", allowed: true }],
      exclusivity: "non_exclusive",
      effectiveAt: "2030-01-02T00:00:00Z",
      terminatesAt: "2030-01-15T00:00:00Z",
      compensation: {
        determinationMethod: "none for showing-only access",
        objectivelyAscertainable: true,
        negotiabilityDisclosurePresent: true,
      },
      signatureEvidence: [
        { signerPartyId: input.buyerPartyId, signedAt: "2030-01-01T23:00:00Z", evidenceId: "agreement-artifact" },
      ],
      executedArtifactId: "artifact-showing",
      executedArtifactDigest: sha256Digest("showing-agreement"),
      executionState: "effective",
    },
    now,
  );
}

export async function createRepresentationAgreement(
  sql: Sql,
  input: {
    tenantId: string;
    licenseHolderId: string;
    brokerageId: string;
    buyerPartyId: string;
    now?: Date;
  },
) {
  const now = input.now ?? NOW;
  return insertCanonical(
    sql,
    {
      ...canonicalEnvelope({
        tenantId: input.tenantId,
        recordType: "WrittenBuyerAgreement",
        status: "effective",
        createdBy: { actorType: "license_holder", actorId: input.licenseHolderId },
        sourceEvidenceIds: ["rep-artifact"],
        now,
      }),
      agreementType: "representation",
      jurisdiction: "TX",
      brokerPartyId: input.brokerageId,
      responsibleLicenseHolderId: input.licenseHolderId,
      buyerPartyIds: [input.buyerPartyId],
      serviceDefinitions: [
        { serviceCode: "showing_access", allowed: true },
        { serviceCode: "property_advice", allowed: true },
        { serviceCode: "offer_presentation", allowed: true },
      ],
      exclusivity: "exclusive",
      effectiveAt: "2030-01-02T00:00:00Z",
      terminatesAt: "2030-07-02T00:00:00Z",
      compensation: {
        amountOrRate: "2.5 percent of purchase price",
        objectivelyAscertainable: true,
        negotiabilityDisclosurePresent: true,
      },
      signatureEvidence: [
        { signerPartyId: input.buyerPartyId, signedAt: "2030-01-01T23:00:00Z", evidenceId: "rep-artifact" },
      ],
      executedArtifactId: "artifact-rep",
      executedArtifactDigest: sha256Digest("rep-agreement"),
      executionState: "effective",
    },
    now,
  );
}

export function formEnvelope(overrides: Partial<{
  providerEventId: string;
  body: string;
  fullName: string;
  email: string;
  phone: string;
  zone: string;
}> = {}) {
  const body = overrides.body ?? "Relocating to San Antonio this fall. Budget around 850k.";
  return {
    schemaVersion: "ot01.inbound/1" as const,
    providerEventId: overrides.providerEventId ?? newId(),
    providerAccountRef: "form.local",
    channel: "form" as const,
    receivedAt: iso(NOW),
    senderEndpoint: overrides.email ?? "buyer@example.com",
    recipientEndpoint: "intake@atono.example",
    payloadArtifactId: newId(),
    payloadDigest: sha256Digest(body),
    signatureVerification: "not_supported" as const,
    body,
    fullName: overrides.fullName ?? "Elena Vasquez",
    email: overrides.email ?? "elena.test@example.com",
    phone: overrides.phone,
    zone: overrides.zone ?? "San Antonio",
  };
}
