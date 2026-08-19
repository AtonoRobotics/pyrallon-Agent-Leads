import type { Sql } from "@/lib/db";
import { ActivationBlocked, ContractViolation } from "@/lib/contracts/violations.ts";
import { canonicalEnvelope, iso, newId, sha256Digest } from "@/lib/canonical/envelope.ts";
import { getRecord, insertCanonical, listRecords, type StoredRecord } from "@/lib/canonical/store.ts";
import { appendEvidence } from "@/lib/evidence/ledger.ts";
import { signalWorkflow, startJourneyWorkflow } from "@/lib/workflow/runtime.ts";
import { logEvent } from "@/lib/observability/log.ts";
import type { PersonRecord } from "@/lib/canonical/predicates.ts";

export type InboundEnvelope = {
  schemaVersion: "ot01.inbound/1";
  providerEventId: string;
  providerAccountRef: string;
  channel: "form" | "email" | "sms";
  receivedAt: string;
  senderEndpoint: string;
  recipientEndpoint: string;
  payloadArtifactId: string;
  payloadDigest: string;
  signatureVerification: "verified" | "not_supported";
  body: string;
  fullName?: string;
  email?: string;
  phone?: string;
  zone?: string;
};

export async function admitInbound(
  sql: Sql,
  input: {
    tenantId: string;
    licenseHolderId: string;
    envelope: InboundEnvelope;
    now?: Date;
  },
) {
  const { envelope, tenantId } = input;
  if (envelope.schemaVersion !== "ot01.inbound/1") {
    throw new ContractViolation([
      { code: "UNSUPPORTED_INBOUND_SCHEMA", path: "$.schemaVersion", message: "expected ot01.inbound/1" },
    ]);
  }
  if (envelope.channel !== "form") {
    const grant = await sql<{ status: string }>`
      select status from connector_grants
      where tenant_id = ${tenantId} and channel = ${envelope.channel} and status = ${"active"}
    `;
    if (!grant[0]) {
      throw new ActivationBlocked(
        "CONNECTOR_INACTIVE",
        `Inbound ${envelope.channel} requires an active governed connector.`,
      );
    }
    if (envelope.signatureVerification !== "verified") {
      throw new ContractViolation([
        {
          code: "SIGNATURE_REQUIRED",
          path: "$.signatureVerification",
          message: "email/SMS ingress requires verified webhook signatures",
        },
      ]);
    }
  }

  const dup = await sql<{ provider_event_id: string; journey_id: string | null }>`
    select provider_event_id, journey_id from inbound_events
    where tenant_id = ${tenantId}
      and provider_account_ref = ${envelope.providerAccountRef}
      and provider_event_id = ${envelope.providerEventId}
  `;
  if (dup[0]) {
    return { outcome: "duplicate" as const, journeyId: dup[0].journey_id, personId: null, stop: false };
  }

  const now = input.now ?? new Date();
  const evidence = await appendEvidence(sql, {
    tenantId,
    payload: { kind: "inbound_envelope", envelope },
    retentionClass: "source_artifact",
    now,
  });

  const stop = envelope.body.trim().toUpperCase() === "STOP";
  const email = envelope.email?.trim().toLowerCase() || undefined;
  const phone = envelope.phone?.trim() || undefined;
  const createdBy = { actorType: "license_holder" as const, actorId: input.licenseHolderId };

  let person: PersonRecord | null = null;
  let identityOutcome: "matched" | "created" | "ambiguous" = "created";

  const lookup = email
    ? await sql<{ person_id: string }>`
        select person_id from endpoint_index
        where tenant_id = ${tenantId} and endpoint_type = ${"email"} and normalized_value = ${email}
      `
    : phone
      ? await sql<{ person_id: string }>`
          select person_id from endpoint_index
          where tenant_id = ${tenantId} and endpoint_type = ${"phone"} and normalized_value = ${phone}
        `
      : [];

  if (lookup[0]) {
    person = await getRecord<PersonRecord>(sql, tenantId, lookup[0].person_id);
    identityOutcome = person?.identityState === "ambiguous" ? "ambiguous" : "matched";
  }

  if (!person) {
    const personId = newId();
    const endpoints = [];
    if (email) {
      endpoints.push({
        endpointId: newId(),
        type: "email" as const,
        normalizedValue: email,
        verificationState: "provider_observed" as const,
        status: "active" as const,
      });
    }
    if (phone) {
      endpoints.push({
        endpointId: newId(),
        type: "phone" as const,
        normalizedValue: phone,
        verificationState: "unverified" as const,
        status: "active" as const,
      });
    }
    person = (await insertCanonical(
      sql,
      {
        ...canonicalEnvelope({
          id: personId,
          tenantId,
          recordType: "Person",
          status: "active",
          createdBy,
          sourceEvidenceIds: [evidence.id],
          now,
        }),
        identityState: "resolved",
        displayName: envelope.fullName?.trim() || envelope.senderEndpoint,
        endpoints,
      },
      now,
    )) as PersonRecord;
    await indexEndpoints(sql, tenantId, person, now);
  }
  if (!person) throw new Error("person missing");

  const parties = await listRecords<StoredRecord & { members: Array<{ personId: string; role: string }> }>(
    sql,
    tenantId,
    "BuyingParty",
  );
  let party: (StoredRecord & { members: Array<{ personId: string; role: string }> }) | undefined =
    parties.find((p) => p.members.some((m) => m.personId === person.id));
  if (!party) {
    party = (await insertCanonical(
      sql,
      {
        ...canonicalEnvelope({
          tenantId,
          recordType: "BuyingParty",
          status: "active",
          createdBy,
          sourceEvidenceIds: [evidence.id],
          now,
        }),
        members: [{ personId: person.id, role: "buyer" }],
      },
      now,
    )) as StoredRecord & { members: Array<{ personId: string; role: string }> };
  }
  if (!party) throw new Error("buying party missing");

  const journeys = (
    await listRecords<StoredRecord & { buyingPartyId: string; journeyState: string }>(
      sql,
      tenantId,
      "BuyerJourney",
    )
  ).filter((j) => j.buyingPartyId === party.id);
  let journey: (StoredRecord & { buyingPartyId: string; journeyState: string }) | undefined =
    journeys.find((j) => !["closed", "released", "ineligible"].includes(j.journeyState));
  if (!journey) {
    journey = (await insertCanonical(
      sql,
      {
        ...canonicalEnvelope({
          tenantId,
          recordType: "BuyerJourney",
          status: "active",
          createdBy,
          sourceEvidenceIds: [evidence.id],
          now,
        }),
        buyingPartyId: party.id,
        ownerLicenseHolderId: input.licenseHolderId,
        journeyState: stop ? "suppressed" : identityOutcome === "ambiguous" ? "blocked" : "captured",
        qualificationState: "collecting",
        representationState: "unconfirmed",
      },
      now,
    )) as StoredRecord & { buyingPartyId: string; journeyState: string };
    await sql`
      insert into journey_ops (
        tenant_id, journey_id, source_channel, source_detail, service_zone,
        contactability, acknowledgment_state, consultation_state, nurture_state, blocker_codes
      ) values (
        ${tenantId}, ${journey.id}, ${envelope.channel}, ${"ot01 inbound"}, ${envelope.zone ?? null},
        ${stop ? "suppressed" : identityOutcome === "ambiguous" ? "unknown" : "contactable"},
        ${"not_sent"}, ${identityOutcome === "ambiguous" ? "blocked" : "not_ready"}, ${"inactive"},
        ${JSON.stringify(
          stop ? ["suppressed"] : identityOutcome === "ambiguous" ? ["identity_unresolved"] : ["connector_not_activated"],
        )}
      )
    `;
  } else if (stop) {
    await sql`
      update journey_ops
      set contactability = ${"suppressed"},
          blocker_codes = ${JSON.stringify(["suppressed"])}
      where tenant_id = ${tenantId} and journey_id = ${journey.id}
    `;
  }
  if (!journey) throw new Error("journey missing");

  if (identityOutcome === "ambiguous") {
    await sql`
      insert into identity_resolution_cases (
        tenant_id, id, person_id, journey_id, status, detail, created_at
      ) values (
        ${tenantId}, ${newId()}, ${person.id}, ${journey.id}, ${"open"},
        ${"Inbound matched an ambiguous identity. No merge. Confirm an endpoint before material mutation."},
        ${iso(now)}
      )
    `;
  }

  await sql`
    insert into operational_messages (
      tenant_id, id, journey_id, conversation_id, direction, channel, body, delivery_state, evidence_id, created_at
    ) values (
      ${tenantId}, ${newId()}, ${journey.id}, ${newId()}, ${"inbound"}, ${envelope.channel},
      ${envelope.body}, ${"received"}, ${evidence.id}, ${iso(now)}
    )
  `;

  await sql`
    insert into inbound_events (
      tenant_id, provider_account_ref, provider_event_id, channel, envelope, payload_digest, journey_id, admitted_at
    ) values (
      ${tenantId}, ${envelope.providerAccountRef}, ${envelope.providerEventId}, ${envelope.channel},
      ${JSON.stringify(envelope)}, ${envelope.payloadDigest || sha256Digest(envelope.body)},
      ${journey.id}, ${iso(now)}
    )
  `;

  if (stop) {
    await insertCanonical(
      sql,
      {
        ...canonicalEnvelope({
          tenantId,
          recordType: "Suppression",
          status: "active",
          createdBy,
          sourceEvidenceIds: [evidence.id],
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
  } else if (identityOutcome === "created") {
    await insertCanonical(
      sql,
      {
        ...canonicalEnvelope({
          tenantId,
          recordType: "ConsentGrant",
          status: "active",
          createdBy,
          sourceEvidenceIds: [evidence.id],
          now,
        }),
        personId: person.id,
        channel: envelope.channel,
        purpose: "transactional_inquiry",
        principalId: input.licenseHolderId,
        basis: "affirmative",
        grantedAt: iso(now),
        presentationEvidenceId: evidence.id,
        validityState: "active",
      },
      now,
    );
  }

  const started = await startJourneyWorkflow(sql, { tenantId, journeyId: String(journey.id), now });
  const workflowId = started.workflowId;
  if (stop) {
    await signalWorkflow(sql, {
      tenantId,
      workflowId,
      eventType: "Blocked",
      eventId: `stop:${envelope.providerEventId}`,
      payload: { code: "suppressed" },
      now,
    });
  } else if (identityOutcome === "ambiguous") {
    await signalWorkflow(sql, {
      tenantId,
      workflowId,
      eventType: "IdentityAmbiguous",
      eventId: `identity:${envelope.providerEventId}`,
      payload: {},
      now,
    });
  }

  await logEvent(sql, {
    tenantId,
    kind: "inbound_admitted",
    journeyId: String(journey.id),
    payload: { outcome: identityOutcome, channel: envelope.channel, stop },
    now,
  });

  return {
    outcome: identityOutcome,
    journeyId: String(journey.id),
    personId: person.id,
    stop,
  };
}

async function indexEndpoints(sql: Sql, tenantId: string, person: PersonRecord, now: Date) {
  for (const endpoint of person.endpoints ?? []) {
    await sql`
      insert into endpoint_index (tenant_id, endpoint_type, normalized_value, person_id, created_at)
      values (${tenantId}, ${endpoint.type}, ${endpoint.normalizedValue}, ${person.id}, ${iso(now)})
      on conflict (tenant_id, endpoint_type, normalized_value) do nothing
    `;
  }
}

export function digestBody(body: string) {
  return sha256Digest(body);
}
