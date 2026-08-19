import { createServerFn } from "@tanstack/react-start";
import { getSql } from "@/lib/db";
import { authMiddleware } from "@/lib/auth/middleware";
import { ActivationBlocked, ContractViolation } from "@/lib/contracts/violations";
import { admitOntologyRecord } from "@/lib/contracts/validate";
import { seedCanonical } from "@/lib/canonical/seed";
import {
  getRecord,
  insertCanonical,
  listByField,
  listRecords,
  updateCanonical,
  type StoredRecord,
} from "@/lib/canonical/store";
import {
  assertNotModelFact,
  consultationReady,
  mayContact,
  type ConsentRecord,
  type EpistemicRecord,
  type JourneyRecord,
  type ObservationRecord,
  type PersonRecord,
  type SuppressionRecord,
} from "@/lib/canonical/predicates";
import { canonicalEnvelope, iso, newId, type ActorRef } from "@/lib/canonical/envelope";
import { bootstrapPackets } from "@/lib/packets/bootstrap";
import { ACTIVATION, PACKETS, connectorInventoryView } from "@/lib/packets/registry";
import { listGates } from "@/lib/packets/gates";
import { admitInbound, digestBody } from "@/lib/ingress/admit";
import { admitIntent } from "@/lib/habitat/service";
import { invokeProvider } from "@/lib/connectors/gateway";
import { listLiveEvidence } from "@/lib/evidence/ledger";
import { replay } from "@/lib/workflow/runtime";
import {
  applyProposal,
  compileContext,
  compileWorkRequest,
  deterministicQualificationProposal,
} from "@/lib/cognition/compiler";
import { localPolicySlots, persistSlotSet } from "@/lib/slots/policy";
import type {
  AppointmentView,
  CaseView,
  DashboardPayload,
  JourneyCard,
  JourneyDetail,
  JourneyView,
  PersonView,
  Tenant,
} from "./types";

function failActivation(what: string): never {
  throw new ActivationBlocked(
    "CONNECTOR_INACTIVE",
    `${what} requires a live governed connector. Email, SMS, and calendar grants are inactive. Voice is prohibited.`,
  );
}

async function ensureTenant(tenantId: string, agentName?: string) {
  const sql = await getSql();
  await seedCanonical({
    sql,
    tenantId,
    agentName: agentName?.trim() || "Sponsored agent",
  });
  await bootstrapPackets(sql, tenantId);
  return sql;
}

async function tenantOf(sql: Awaited<ReturnType<typeof getSql>>, tenantId: string): Promise<Tenant> {
  const rows = await sql<{
    tenant_id: string;
    brokerage_name: string;
    agent_name: string;
    license_number: string;
    license_holder_id: string;
    brokerage_id: string;
  }>`
    select tenant_id, brokerage_name, agent_name, license_number, license_holder_id, brokerage_id
    from tenant_profiles where tenant_id = ${tenantId}
  `;
  const row = rows[0];
  if (!row) throw new Error("Tenant profile missing");
  return {
    tenantId: row.tenant_id,
    brokerageName: row.brokerage_name,
    agentName: row.agent_name,
    licenseNumber: row.license_number,
    licenseHolderId: row.license_holder_id,
    brokerageId: row.brokerage_id,
  };
}

function actor(licenseHolderId: string): ActorRef {
  return { actorType: "license_holder", actorId: licenseHolderId };
}

function personView(person: PersonRecord): PersonView {
  return {
    id: person.id,
    displayName: person.displayName,
    identityState: person.identityState,
    email: person.endpoints.find((e) => e.type === "email")?.normalizedValue ?? null,
    phone: person.endpoints.find((e) => e.type === "phone")?.normalizedValue ?? null,
  };
}

type OpsRow = {
  journey_id: string;
  source_channel: string;
  source_detail: string | null;
  service_zone: string | null;
  contactability: string;
  acknowledgment_state: string;
  consultation_state: string;
  nurture_state: string;
  blocker_codes: string;
};

function parseCodes(raw: string): string[] {
  try {
    const v = JSON.parse(raw) as unknown;
    return Array.isArray(v) ? v.map(String) : [];
  } catch {
    return [];
  }
}

function journeyView(journey: JourneyRecord, ops: OpsRow): JourneyView {
  return {
    id: journey.id,
    personId: "",
    buyingPartyId: journey.buyingPartyId,
    journeyState: journey.journeyState,
    qualificationState: journey.qualificationState,
    representationState: journey.representationState,
    source: ops.source_channel,
    sourceDetail: ops.source_detail,
    serviceZone: ops.service_zone,
    contactability: ops.contactability,
    acknowledgment: ops.acknowledgment_state,
    consultationState: ops.consultation_state,
    nurtureState: ops.nurture_state,
    blockerCodes: parseCodes(ops.blocker_codes),
    createdAt: String(journey.createdAt),
    updatedAt: String(journey.updatedAt),
  };
}

export const getDashboard = createServerFn({ method: "GET" })
  .middleware([authMiddleware])
  .handler(async ({ context }) => {
    const sql = await ensureTenant(context.userId);
    const tenant = await tenantOf(sql, context.userId);
    const journeys = await listRecords<JourneyRecord>(sql, context.userId, "BuyerJourney");
    const people = await listRecords<PersonRecord>(sql, context.userId, "Person");
    const parties = await listRecords<StoredRecord & { members: Array<{ personId: string }> }>(
      sql,
      context.userId,
      "BuyingParty",
    );
    const appointments = await listRecords<
      StoredRecord & { journeyId: string; startsAt: string; endsAt: string; appointmentState: string; locationOrMode?: string }
    >(sql, context.userId, "Appointment");
    const opsRows = await sql<OpsRow>`
      select * from journey_ops where tenant_id = ${context.userId}
    `;
    const cases = await sql<{
      id: string;
      journey_id: string | null;
      status: string;
      detail: string;
      created_at: string;
    }>`
      select id, journey_id, status, detail, created_at
      from identity_resolution_cases where tenant_id = ${context.userId}
      order by created_at desc
    `;
    const opsByJourney = new Map(opsRows.map((row) => [row.journey_id, row]));
    const partyById = new Map(parties.map((p) => [p.id, p]));
    const personById = new Map(people.map((p) => [p.id, p]));

    const caseViews: CaseView[] = cases.map((c) => ({
      id: c.id,
      journeyId: c.journey_id,
      kind: "identity_ambiguous",
      title: "Identity resolution required",
      detail: c.detail,
      status: c.status,
      createdAt: c.created_at,
    }));

    const cards: JourneyCard[] = [];
    for (const journey of journeys) {
      const ops = opsByJourney.get(journey.id);
      if (!ops) continue;
      const party = partyById.get(journey.buyingPartyId);
      const person = party ? personById.get(party.members[0]?.personId ?? "") : undefined;
      if (!person) continue;
      const next = appointments
        .filter((a) => a.journeyId === journey.id)
        .sort((a, b) => a.startsAt.localeCompare(b.startsAt))[0];
      const view = journeyView(journey, ops);
      view.personId = person.id;
      cards.push({
        ...view,
        person: personView(person),
        openCases: caseViews.filter((c) => c.journeyId === journey.id && c.status === "open").length,
        nextAppointment: next
          ? {
              id: next.id,
              startsAt: next.startsAt,
              state: next.appointmentState,
              locationOrMode: next.locationOrMode ?? null,
            }
          : null,
      });
    }

    const appointmentViews: AppointmentView[] = appointments.map((a) => ({
      id: a.id,
      journeyId: a.journeyId,
      startsAt: a.startsAt,
      endsAt: a.endsAt,
      state: a.appointmentState,
      locationOrMode: a.locationOrMode ?? null,
    }));

    const payload: DashboardPayload = {
      tenant,
      journeys: cards,
      cases: caseViews,
      appointments: appointmentViews,
      stats: {
        active: cards.filter((j) => !["suppressed", "released"].includes(j.journeyState)).length,
        ready: cards.filter((j) => j.journeyState === "consultation_ready").length,
        proposed: appointmentViews.filter((a) => a.state === "proposed").length,
        openCases: caseViews.filter((c) => c.status === "open").length,
        suppressed: cards.filter((j) => j.contactability === "suppressed").length,
      },
      activation: ACTIVATION,
    };
    return payload;
  });

export const getJourney = createServerFn({ method: "GET" })
  .middleware([authMiddleware])
  .validator((id: string) => id)
  .handler(async ({ context, data: id }) => {
    const sql = await ensureTenant(context.userId);
    const tenant = await tenantOf(sql, context.userId);
    const journey = await getRecord<JourneyRecord>(sql, context.userId, id);
    if (!journey || journey.recordType !== "BuyerJourney") throw new Error("Journey not found");
    const opsRows = await sql<OpsRow>`
      select * from journey_ops where tenant_id = ${context.userId} and journey_id = ${id}
    `;
    if (!opsRows[0]) throw new Error("Journey ops projection missing");
    const party = await getRecord<StoredRecord & { members: Array<{ personId: string }> }>(
      sql,
      context.userId,
      journey.buyingPartyId,
    );
    const person = party
      ? await getRecord<PersonRecord>(sql, context.userId, party.members[0].personId)
      : null;
    if (!person) throw new Error("Person not found");

    const observations = await listByField<ObservationRecord>(
      sql,
      context.userId,
      "QualificationObservation",
      "journeyId",
      id,
    );
    const epistemics = await listRecords<EpistemicRecord>(sql, context.userId, "EpistemicItem");
    const byEpistemic = new Map(epistemics.map((e) => [e.id, e]));
    const appointments = (
      await listByField<
        StoredRecord & {
          journeyId: string;
          startsAt: string;
          endsAt: string;
          appointmentState: string;
          locationOrMode?: string;
        }
      >(sql, context.userId, "Appointment", "journeyId", id)
    ).map((a) => ({
      id: a.id,
      journeyId: a.journeyId,
      startsAt: a.startsAt,
      endsAt: a.endsAt,
      state: a.appointmentState,
      locationOrMode: a.locationOrMode ?? null,
    }));
    const consents = (
      await listByField<ConsentRecord>(sql, context.userId, "ConsentGrant", "personId", person.id)
    ).map((c) => ({
      id: c.id,
      channel: c.channel,
      purpose: c.purpose,
      status: c.validityState,
      basis: String(c.basis ?? ""),
    }));
    const commitments = (
      await listByField<
        StoredRecord & { description: string; commitmentState: string; dueAt: string }
      >(sql, context.userId, "Commitment", "journeyId", id)
    ).map((c) => ({
      id: c.id,
      description: c.description,
      state: c.commitmentState,
      dueAt: c.dueAt,
    }));
    const messages = (
      await sql<{
        id: string;
        direction: string;
        channel: string;
        body: string;
        delivery_state: string;
        created_at: string;
      }>`
        select id, direction, channel, body, delivery_state, created_at
        from operational_messages
        where tenant_id = ${context.userId} and journey_id = ${id}
        order by created_at asc
      `
    ).map((m) => ({
      id: m.id,
      direction: m.direction,
      channel: m.channel,
      body: m.body,
      deliveryState: m.delivery_state,
      createdAt: m.created_at,
    }));
    const cases = (
      await sql<{
        id: string;
        journey_id: string | null;
        status: string;
        detail: string;
        created_at: string;
      }>`
        select id, journey_id, status, detail, created_at
        from identity_resolution_cases
        where tenant_id = ${context.userId} and journey_id = ${id}
      `
    ).map((c) => ({
      id: c.id,
      journeyId: c.journey_id,
      kind: "identity_ambiguous",
      title: "Identity resolution required",
      detail: c.detail,
      status: c.status,
      createdAt: c.created_at,
    }));

    const view = journeyView(journey, opsRows[0]);
    view.personId = person.id;
    const detail: JourneyDetail = {
      tenant,
      journey: view,
      person: personView(person),
      messages,
      observations: observations.map((o) => {
        const item = byEpistemic.get(o.epistemicItemId);
        return {
          id: o.id,
          criterion: o.criterionId,
          epistemicType: item?.epistemicType ?? "unknown",
          value: item ? String(item.proposition.value) : "",
          observationState: o.observationState,
          sourceLabel: item?.speakerOrMethodRef ?? null,
        };
      }),
      appointments,
      cases,
      evidence: epistemics
        .filter((e) => e.epistemicType === "evidence")
        .slice(0, 12)
        .map((e) => ({
          id: e.id,
          summary: String(e.proposition.value),
          epistemicType: e.epistemicType,
        })),
      consent: consents,
      commitments,
      activation: ACTIVATION,
      workflow: {
        workflowId: `buyer-journey:${id}`,
        state: await replay(sql, context.userId, `buyer-journey:${id}`).catch(() => ({
          ingressState: "unknown",
          acknowledgmentState: "unknown",
          qualificationState: "unknown",
          consultationState: "unknown",
          nurtureState: "unknown",
          blockerCodes: [],
        })),
      },
      ledger: (await listLiveEvidence(sql, context.userId, id)).map((row) => ({
        id: row.id,
        seq: row.seq,
        kind: String(row.payload.kind ?? "entry"),
      })),
    };
    return detail;
  });

export const captureLead = createServerFn({ method: "POST" })
  .middleware([authMiddleware])
  .validator((input: {
    fullName: string;
    email?: string;
    phone?: string;
    zone: string;
    channel: "form";
    message: string;
    intent?: string;
    budget?: string;
    timing?: string;
  }) => input)
  .handler(async ({ context, data }) => {
    if (data.channel !== "form") {
      failActivation(`Inbound ${data.channel} capture`);
    }
    const sql = await ensureTenant(context.userId);
    const tenant = await tenantOf(sql, context.userId);
    const name = data.fullName.trim();
    const body = data.message.trim();
    if (!name || !body) throw new Error("Name and message are required");
    const now = new Date();
    const admitted = await admitInbound(sql, {
      tenantId: context.userId,
      licenseHolderId: tenant.licenseHolderId,
      envelope: {
        schemaVersion: "ot01.inbound/1",
        providerEventId: newId(),
        providerAccountRef: "form.local",
        channel: "form",
        receivedAt: iso(now),
        senderEndpoint: data.email?.trim().toLowerCase() || data.phone?.trim() || name,
        recipientEndpoint: "intake@atono.example",
        payloadArtifactId: newId(),
        payloadDigest: digestBody(body),
        signatureVerification: "not_supported",
        body,
        fullName: name,
        email: data.email?.trim().toLowerCase() || undefined,
        phone: data.phone?.trim() || undefined,
        zone: data.zone,
      },
      now,
    });
    if (!admitted.journeyId) {
      throw new Error("Inbound event admitted without a journey");
    }
    if (admitted.outcome === "duplicate" || !admitted.personId) {
      return { journeyId: admitted.journeyId };
    }

    const extras: Array<[string, string]> = [];
    if (data.intent?.trim()) extras.push(["purchase_intent", data.intent.trim()]);
    if (data.budget?.trim()) extras.push(["budget_financing", data.budget.trim()]);
    if (data.timing?.trim()) extras.push(["timing", data.timing.trim()]);
    extras.push(["geography", data.zone]);
    const createdBy = actor(tenant.licenseHolderId);
    const existing = await listByField<ObservationRecord>(
      sql,
      context.userId,
      "QualificationObservation",
      "journeyId",
      admitted.journeyId,
    );
    const have = new Set(existing.map((o) => o.criterionId));
    for (const [criterion, value] of extras) {
      if (have.has(criterion)) continue;
      const itemId = newId();
      await insertCanonical(sql, {
        ...canonicalEnvelope({
          id: itemId,
          tenantId: context.userId,
          recordType: "EpistemicItem",
          status: "current",
          createdBy,
          sourceEvidenceIds: [itemId],
          now,
        }),
        epistemicType: "assertion",
        proposition: {
          subjectRef: admitted.personId,
          predicate: criterion,
          value,
          applicableJourneyId: admitted.journeyId,
          validFrom: iso(now),
        },
        speakerOrMethodRef: admitted.personId,
        validityState: "current",
      }, now);
      await insertCanonical(sql, {
        ...canonicalEnvelope({
          tenantId: context.userId,
          recordType: "QualificationObservation",
          status: "current",
          createdBy,
          sourceEvidenceIds: [itemId],
          now,
        }),
        journeyId: admitted.journeyId,
        criterionId: criterion,
        epistemicItemId: itemId,
        observationState: "asserted",
      }, now);
      have.add(criterion);
    }

    try {
      const compiled = await compileContext(sql, {
        tenantId: context.userId,
        journeyId: admitted.journeyId,
        now,
      });
      const request = compileWorkRequest({
        tenantId: context.userId,
        principalId: tenant.licenseHolderId,
        journeyId: admitted.journeyId,
        compiled,
        now,
      });
      const proposal = deterministicQualificationProposal({
        workId: request.workId,
        contextManifestId: compiled.manifestId,
        personId: admitted.personId,
        journeyId: admitted.journeyId,
        message: body,
        sourceIds: compiled.sourceRecordIds,
        now,
      });
      await applyProposal(sql, {
        tenantId: context.userId,
        licenseHolderId: tenant.licenseHolderId,
        journeyId: admitted.journeyId,
        personId: admitted.personId,
        proposal,
        now,
      });
    } catch (err) {
      if (!(err instanceof ContractViolation)) throw err;
    }

    return { journeyId: admitted.journeyId };
  });

export const recordAssertion = createServerFn({ method: "POST" })
  .middleware([authMiddleware])
  .validator((input: { journeyId: string; criterion: string; value: string }) => input)
  .handler(async ({ context, data }) => {
    const sql = await ensureTenant(context.userId);
    const tenant = await tenantOf(sql, context.userId);
    const journey = await getRecord<JourneyRecord>(sql, context.userId, data.journeyId);
    if (!journey) throw new Error("Journey not found");
    const party = await getRecord<StoredRecord & { members: Array<{ personId: string }> }>(
      sql,
      context.userId,
      journey.buyingPartyId,
    );
    const personId = party?.members[0]?.personId;
    if (!personId) throw new Error("Person not found");
    const now = new Date();
    const createdBy = actor(tenant.licenseHolderId);
    const itemId = newId();
    const item = {
      ...canonicalEnvelope({
        id: itemId,
        tenantId: context.userId,
        recordType: "EpistemicItem",
        status: "current",
        createdBy,
        sourceEvidenceIds: [itemId],
        now,
      }),
      epistemicType: "assertion",
      proposition: {
        subjectRef: personId,
        predicate: data.criterion,
        value: data.value.trim(),
        applicableJourneyId: data.journeyId,
        validFrom: iso(now),
      },
      speakerOrMethodRef: personId,
      validityState: "current",
    };
    assertNotModelFact(item);
    await insertCanonical(sql, item, now);
    await insertCanonical(sql, {
      ...canonicalEnvelope({
        tenantId: context.userId,
        recordType: "QualificationObservation",
        status: "current",
        createdBy,
        sourceEvidenceIds: [itemId],
        now,
      }),
      journeyId: data.journeyId,
      criterionId: data.criterion,
      epistemicItemId: itemId,
      observationState: "asserted",
    }, now);
    return { ok: true as const };
  });

export const resolveIdentity = createServerFn({ method: "POST" })
  .middleware([authMiddleware])
  .validator((input: { caseId: string; note: string }) => input)
  .handler(async ({ context, data }) => {
    const sql = await ensureTenant(context.userId);
    const rows = await sql<{ id: string; person_id: string; journey_id: string | null; status: string }>`
      select id, person_id, journey_id, status from identity_resolution_cases
      where tenant_id = ${context.userId} and id = ${data.caseId}
    `;
    if (!rows[0] || rows[0].status !== "open") throw new Error("Case not found");
    if (!data.note.trim()) throw new Error("Resolution note is required");
    const person = await getRecord<PersonRecord>(sql, context.userId, rows[0].person_id);
    if (!person) throw new Error("Person not found");
    await updateCanonical(sql, context.userId, person, { identityState: "resolved" });
    await sql`
      update identity_resolution_cases
      set status = ${"resolved"}, resolved_at = now(), resolution_note = ${data.note.trim()}
      where tenant_id = ${context.userId} and id = ${data.caseId}
    `;
    if (rows[0].journey_id) {
      const journey = await getRecord<JourneyRecord>(sql, context.userId, rows[0].journey_id);
      if (journey) {
        await updateCanonical(sql, context.userId, journey, {
          journeyState: "qualifying",
          qualificationState: "collecting",
        });
        await sql`
          update journey_ops
          set contactability = ${"contactable"},
              consultation_state = ${"not_ready"},
              blocker_codes = ${JSON.stringify(["connector_not_activated"])}
          where tenant_id = ${context.userId} and journey_id = ${rows[0].journey_id}
        `;
      }
    }
    return { ok: true as const };
  });

export const resolveRepresentation = createServerFn({ method: "POST" })
  .middleware([authMiddleware])
  .validator((input: { journeyId: string; note: string }) => input)
  .handler(async ({ context, data }) => {
    const sql = await ensureTenant(context.userId);
    const tenant = await tenantOf(sql, context.userId);
    const journey = await getRecord<JourneyRecord>(sql, context.userId, data.journeyId);
    if (!journey) throw new Error("Journey not found");
    if (!data.note.trim()) throw new Error("Inspection note is required");
    const agreements = await listRecords<StoredRecord>(sql, context.userId, "WrittenBuyerAgreement");
    const covering = agreements.filter((a) => {
      const rec = a as StoredRecord & { buyerPartyIds?: string[]; executionState?: string };
      return rec.buyerPartyIds?.includes(journey.buyingPartyId) && rec.executionState === "effective";
    });
    if (covering.length > 1) {
      throw new ContractViolation([
        {
          code: "REPRESENTATION_STILL_CONFLICTED",
          path: "$.WrittenBuyerAgreement",
          message: "More than one effective agreement still covers this party.",
        },
      ]);
    }
    const now = new Date();
    const itemId = newId();
    const item = {
      ...canonicalEnvelope({
        id: itemId,
        tenantId: context.userId,
        recordType: "EpistemicItem",
        status: "current",
        createdBy: actor(tenant.licenseHolderId),
        sourceEvidenceIds: [itemId],
        now,
      }),
      epistemicType: "verified_fact",
      proposition: {
        subjectRef: journey.buyingPartyId,
        predicate: "representation",
        value: covering[0]
          ? `Effective agreement ${covering[0].id}`
          : data.note.trim(),
        applicableJourneyId: journey.id,
        validFrom: iso(now),
      },
      speakerOrMethodRef: tenant.licenseHolderId,
      validityState: "current",
    };
    assertNotModelFact(item);
    await insertCanonical(sql, item, now);
    await updateCanonical(sql, context.userId, journey, {
      representationState: covering[0] ? "represented" : "not_represented",
      journeyState: "qualifying",
      qualificationState: "collecting",
    });
    await sql`
      update journey_ops
      set consultation_state = ${"not_ready"},
          nurture_state = ${"inactive"},
          blocker_codes = ${JSON.stringify(["connector_not_activated"])}
      where tenant_id = ${context.userId} and journey_id = ${data.journeyId}
    `;
    return { ok: true as const };
  });

export const recordSuppression = createServerFn({ method: "POST" })
  .middleware([authMiddleware])
  .validator((journeyId: string) => journeyId)
  .handler(async ({ context, data: journeyId }) => {
    const sql = await ensureTenant(context.userId);
    const tenant = await tenantOf(sql, context.userId);
    const journey = await getRecord<JourneyRecord>(sql, context.userId, journeyId);
    if (!journey) throw new Error("Journey not found");
    const party = await getRecord<StoredRecord & { members: Array<{ personId: string }> }>(
      sql,
      context.userId,
      journey.buyingPartyId,
    );
    const person = party
      ? await getRecord<PersonRecord>(sql, context.userId, party.members[0].personId)
      : null;
    if (!person) throw new Error("Person not found");
    const now = new Date();
    await insertCanonical(sql, {
      ...canonicalEnvelope({
        tenantId: context.userId,
        recordType: "Suppression",
        status: "active",
        createdBy: actor(tenant.licenseHolderId),
        sourceEvidenceIds: [],
        now,
      }),
      subjectId: person.id,
      scope: "all_non_required_contact",
      reason: "opt_out",
      suppressedAt: iso(now),
      validityState: "active",
    }, now);
    await updateCanonical(sql, context.userId, journey, { journeyState: "suppressed" });
    await sql`
      update journey_ops
      set contactability = ${"suppressed"},
          consultation_state = ${"not_ready"},
          blocker_codes = ${JSON.stringify(["suppressed"])}
      where tenant_id = ${context.userId} and journey_id = ${journeyId}
    `;
    const contact = mayContact({
      person,
      consents: await listByField<ConsentRecord>(sql, context.userId, "ConsentGrant", "personId", person.id),
      suppressions: await listByField<SuppressionRecord>(sql, context.userId, "Suppression", "subjectId", person.id),
      channel: "email",
      purpose: "transactional_inquiry",
    });
    if (contact.allowed) {
      throw new ContractViolation([
        { code: "SUPPRESSION_NOT_DOMINANT", path: "$.MayContact", message: "suppression must defeat contact" },
      ]);
    }
    return { ok: true as const, mayContact: contact };
  });

export const recomputeReadiness = createServerFn({ method: "POST" })
  .middleware([authMiddleware])
  .validator((journeyId: string) => journeyId)
  .handler(async ({ context, data: journeyId }) => {
    const sql = await ensureTenant(context.userId);
    const journey = await getRecord<JourneyRecord>(sql, context.userId, journeyId);
    if (!journey) throw new Error("Journey not found");
    const party = await getRecord<StoredRecord & { members: Array<{ personId: string }> }>(
      sql,
      context.userId,
      journey.buyingPartyId,
    );
    const person = party
      ? await getRecord<PersonRecord>(sql, context.userId, party.members[0].personId)
      : null;
    if (!person) throw new Error("Person not found");
    const observations = await listByField<ObservationRecord>(
      sql,
      context.userId,
      "QualificationObservation",
      "journeyId",
      journeyId,
    );
    const openCases = await sql<{ id: string }>`
      select id from identity_resolution_cases
      where tenant_id = ${context.userId} and journey_id = ${journeyId} and status = ${"open"}
    `;
    const result = consultationReady({
      person,
      journey,
      observations,
      openIdentityCase: openCases.length > 0,
    });
    await updateCanonical(sql, context.userId, journey, {
      qualificationState: result.ready ? "sufficient_for_consult" : journey.qualificationState === "contradicted" ? "contradicted" : "collecting",
      journeyState: result.ready ? "consultation_ready" : journey.journeyState === "suppressed" ? "suppressed" : "qualifying",
    });
    await sql`
      update journey_ops
      set consultation_state = ${result.ready ? "ready" : "not_ready"},
          blocker_codes = ${JSON.stringify(result.ready ? ["connector_not_activated"] : result.reasons)}
      where tenant_id = ${context.userId} and journey_id = ${journeyId}
    `;
    return result;
  });

export const proposeAppointment = createServerFn({ method: "POST" })
  .middleware([authMiddleware])
  .validator((input: { journeyId: string; startsAt: string }) => input)
  .handler(async ({ context, data }) => {
    const sql = await ensureTenant(context.userId);
    const tenant = await tenantOf(sql, context.userId);
    const journey = await getRecord<JourneyRecord>(sql, context.userId, data.journeyId);
    if (!journey) throw new Error("Journey not found");
    const party = await getRecord<StoredRecord & { members: Array<{ personId: string }> }>(
      sql,
      context.userId,
      journey.buyingPartyId,
    );
    const personId = party?.members[0]?.personId;
    if (!personId) throw new Error("Person not found");
    const now = new Date();
    const start = new Date(data.startsAt);
    const end = new Date(start.getTime() + 45 * 60_000);
    const permit = await admitIntent(sql, {
      tenantId: context.userId,
      actionClass: "propose_local_slot",
      payload: { journeyId: data.journeyId, startsAt: start.toISOString() },
      idempotencyKey: `slot:${data.journeyId}:${start.toISOString()}`,
      resourceKey: `slot:${data.journeyId}`,
      actorId: tenant.licenseHolderId,
      journeyId: data.journeyId,
    });
    const slots = localPolicySlots(now);
    await persistSlotSet(sql, {
      tenantId: context.userId,
      journeyId: data.journeyId,
      slots,
      now,
    });
    await insertCanonical(sql, {
      ...canonicalEnvelope({
        tenantId: context.userId,
        recordType: "Appointment",
        status: "active",
        createdBy: actor(tenant.licenseHolderId),
        sourceEvidenceIds: [permit.permitId],
        now,
      }),
      journeyId: data.journeyId,
      appointmentType: "buyer_consultation",
      participantIds: [personId, tenant.licenseHolderId],
      startsAt: start.toISOString(),
      endsAt: end.toISOString(),
      timeZone: "America/Chicago",
      locationOrMode: "proposed_local_policy_slot",
      appointmentState: "proposed",
    }, now);
    return { state: "proposed" as const, permitId: permit.permitId };
  });

export const denyOutboundEffect = createServerFn({ method: "POST" })
  .middleware([authMiddleware])
  .validator((input: { kind: "ack" | "calendar"; journeyId: string }) => input)
  .handler(async ({ context, data }) => {
    const sql = await ensureTenant(context.userId);
    const tenant = await tenantOf(sql, context.userId);
    const actionClass = data.kind === "ack" ? "outbound_acknowledgment" : "calendar_write";
    const connectorId = data.kind === "ack" ? "email.outbound" : "calendar.write";
    const payload = {
      journeyId: data.journeyId,
      purpose: "transactional_inquiry",
    };
    try {
      const permit = await admitIntent(sql, {
        tenantId: context.userId,
        actionClass,
        payload,
        idempotencyKey: `${actionClass}:${data.journeyId}:${Date.now()}`,
        resourceKey: `${actionClass}:${data.journeyId}`,
        actorId: tenant.licenseHolderId,
        journeyId: data.journeyId,
      });
      await invokeProvider({
        sql,
        tenantId: context.userId,
        permit,
        connectorId,
        payload,
        licenseHolderId: tenant.licenseHolderId,
      });
      return { ok: true as const };
    } catch (err) {
      if (err instanceof ActivationBlocked) {
        return { ok: false as const, code: err.code, message: err.message };
      }
      if (err instanceof ContractViolation) {
        return {
          ok: false as const,
          code: err.violations[0]?.code ?? "HABITAT_DENIED",
          message: err.message,
        };
      }
      throw err;
    }
  });

export const getPacketStatus = createServerFn({ method: "GET" })
  .middleware([authMiddleware])
  .handler(async ({ context }) => {
    const sql = await ensureTenant(context.userId);
    const tenant = await tenantOf(sql, context.userId);
    const gates = await listGates(sql);
    return {
      tenant,
      activation: ACTIVATION,
      packets: PACKETS,
      connectors: connectorInventoryView(),
      gates,
    };
  });

export const validateShowingAgreement = createServerFn({ method: "POST" })
  .middleware([authMiddleware])
  .validator((input: { terminatesAt: string; allowAdvice: boolean }) => input)
  .handler(async ({ context, data }) => {
    const sql = await ensureTenant(context.userId);
    const tenant = await tenantOf(sql, context.userId);
    const now = new Date("2030-01-01T00:00:00Z");
    const record = {
      ...canonicalEnvelope({
        tenantId: context.userId,
        recordType: "WrittenBuyerAgreement",
        status: "effective",
        createdBy: actor(tenant.licenseHolderId),
        sourceEvidenceIds: ["agreement-preview"],
        now,
      }),
      agreementType: "non_representation_showing",
      jurisdiction: "TX",
      brokerPartyId: tenant.brokerageId,
      responsibleLicenseHolderId: tenant.licenseHolderId,
      buyerPartyIds: ["preview-party"],
      serviceDefinitions: data.allowAdvice
        ? [
            { serviceCode: "showing_access", allowed: true },
            { serviceCode: "property_advice", allowed: true },
          ]
        : [{ serviceCode: "showing_access", allowed: true }],
      exclusivity: "non_exclusive",
      effectiveAt: "2030-01-02T00:00:00Z",
      terminatesAt: data.terminatesAt,
      compensation: {
        determinationMethod: "none for showing-only access",
        objectivelyAscertainable: true,
        negotiabilityDisclosurePresent: true,
      },
      signatureEvidence: [
        { signerPartyId: "preview-party", signedAt: "2030-01-01T23:00:00Z", evidenceId: "agreement-preview" },
      ],
      executedArtifactId: "artifact-preview",
      executedArtifactDigest:
        "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      executionState: "effective",
    };
    try {
      admitOntologyRecord(record, now);
      return { ok: true as const, violations: [] as { code: string; message: string }[] };
    } catch (err) {
      if (err instanceof ContractViolation) {
        return {
          ok: false as const,
          violations: err.violations.map((v) => ({ code: v.code, message: v.message })),
        };
      }
      throw err;
    }
  });
