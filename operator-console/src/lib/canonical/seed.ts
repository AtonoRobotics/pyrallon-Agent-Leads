import type { Sql } from "@/lib/db";
import { insertCanonical } from "./store";
import { canonicalEnvelope, iso, newId, sha256Digest, type ActorRef } from "./envelope";

type Ctx = {
  sql: Sql;
  tenantId: string;
  agentName: string;
};

export async function seedCanonical({ sql, tenantId, agentName }: Ctx) {
  const existing = await sql<{ tenant_id: string }>`
    select tenant_id from tenant_profiles where tenant_id = ${tenantId}
  `;
  if (existing[0]) return;

  const licenseHolderId = newId();
  const brokerageId = newId();
  const actor: ActorRef = { actorType: "license_holder", actorId: licenseHolderId };
  const now = new Date();

  await sql`
    insert into tenant_profiles (
      tenant_id, brokerage_name, agent_name, license_number, license_holder_id, brokerage_id, seeded_at
    ) values (
      ${tenantId}, ${"Atono Brokerage"}, ${agentName}, ${"TX-SA-44821"},
      ${licenseHolderId}, ${brokerageId}, now()
    )
  `;

  async function evidence(summary: string) {
    const id = newId();
    const ev = {
      ...canonicalEnvelope({
        id,
        tenantId,
        recordType: "EpistemicItem",
        status: "current",
        createdBy: actor,
        sourceEvidenceIds: [id],
        now,
      }),
      epistemicType: "evidence",
      proposition: {
        subjectRef: tenantId,
        predicate: "source_artifact",
        value: summary,
        validFrom: iso(now),
      },
      validityState: "current",
    };
    await insertCanonical(sql, ev, now);
    return id;
  }

  type Buyer = {
    name: string;
    email?: string;
    phone?: string;
    identity: "resolved" | "ambiguous" | "conflict";
    journeyState: string;
    qual: string;
    rep: string;
    source: string;
    detail: string;
    zone: string;
    contact: string;
    ack: string;
    consult: string;
    nurture: string;
    blockers: string[];
    inbound: string;
    observations: Array<[string, "assertion" | "verified_fact" | "inference", string, string]>;
    stop?: boolean;
    identityCase?: string;
    proposedConsult?: boolean;
  };

  const buyers: Buyer[] = [
    {
      name: "Elena Vasquez",
      email: "elena.vasquez@example.com",
      phone: "+12105550142",
      identity: "resolved",
      journeyState: "consultation_ready",
      qual: "sufficient_for_consult",
      rep: "not_represented",
      source: "form",
      detail: "Buyer consult — Alamo Heights landing",
      zone: "San Antonio",
      contact: "contactable",
      ack: "not_sent",
      consult: "not_ready",
      nurture: "inactive",
      blockers: ["connector_not_activated"],
      inbound:
        "Hi — relocating from Houston to Alamo Heights this fall. Two kids, want a yard, budget around 850k. Can we talk this week?",
      observations: [
        ["purchase_intent", "assertion", "Relocating from Houston this fall", "form:body"],
        ["geography", "assertion", "Alamo Heights / near-in San Antonio", "form:body"],
        ["property", "assertion", "Yard required; family home", "form:body"],
        ["budget_financing", "assertion", "Around $850,000 — financing not verified", "form:body"],
        ["timing", "assertion", "This fall", "form:body"],
      ],
      proposedConsult: true,
    },
    {
      name: "Marcus Hale",
      email: "marcus.hale@example.com",
      identity: "resolved",
      journeyState: "qualifying",
      qual: "collecting",
      rep: "unconfirmed",
      source: "form",
      detail: "East Austin inquiry",
      zone: "Austin",
      contact: "contactable",
      ack: "not_sent",
      consult: "not_ready",
      nurture: "inactive",
      blockers: ["missing_budget_financing", "connector_not_activated"],
      inbound: "Heading to East Austin for work in September. Still selling Dallas. Neighborhoods?",
      observations: [
        ["purchase_intent", "assertion", "Moving to East Austin for work", "form:body"],
        ["geography", "assertion", "East Austin", "form:body"],
        ["timing", "assertion", "September", "form:body"],
        ["contingency", "assertion", "Selling a Dallas home first", "form:body"],
      ],
    },
    {
      name: "Priya Shah",
      email: "priya.shah@example.com",
      identity: "resolved",
      journeyState: "consultation_ready",
      qual: "sufficient_for_consult",
      rep: "not_represented",
      source: "form",
      detail: "Holt family referral captured on branded form",
      zone: "Fredericksburg",
      contact: "contactable",
      ack: "not_sent",
      consult: "not_ready",
      nurture: "inactive",
      blockers: ["connector_not_activated"],
      inbound: "The Holts said we should talk. Hill Country near Fredericksburg. Cash if the acreage is right. Thursday or Friday.",
      observations: [
        ["purchase_intent", "assertion", "Hill Country acreage search", "form:body"],
        ["geography", "assertion", "Near Fredericksburg", "form:body"],
        ["budget_financing", "assertion", "Cash if the right acreage appears — not verified", "form:body"],
        ["timing", "assertion", "This week if a consult is possible", "form:body"],
        ["scheduling", "assertion", "Thursday or Friday", "form:body"],
      ],
    },
    {
      name: "J. Ellis",
      phone: "+12105550110",
      identity: "ambiguous",
      journeyState: "captured",
      qual: "not_started",
      rep: "unconfirmed",
      source: "form",
      detail: "Name-only collision — no merge",
      zone: "San Antonio",
      contact: "unknown",
      ack: "not_sent",
      consult: "blocked",
      nurture: "inactive",
      blockers: ["identity_ambiguous"],
      inbound: "This is Jordan — still looking in SA.",
      observations: [],
      identityCase:
        "Inbound form shares a first-name token with an existing Ellis household. Names cannot merge people. No material mutation until an endpoint is confirmed.",
    },
    {
      name: "Wei Chen",
      email: "wei.chen@example.com",
      identity: "resolved",
      journeyState: "blocked",
      qual: "contradicted",
      rep: "conflict",
      source: "form",
      detail: "Two representation statements",
      zone: "Austin",
      contact: "contactable",
      ack: "not_sent",
      consult: "blocked",
      nurture: "paused",
      blockers: ["representation_conflict"],
      inbound: "We already have someone in Austin but also spoke with another agent last week. Want to see homes this weekend.",
      observations: [
        ["representation", "assertion", "Buyer states a current Austin agent", "form:body"],
        ["representation", "assertion", "Buyer also states a second recent agent conversation", "form:body"],
        ["purchase_intent", "assertion", "Wants to see homes this weekend", "form:body"],
        ["geography", "assertion", "Austin", "form:body"],
      ],
    },
    {
      name: "Sam Ortiz",
      phone: "+12105550133",
      identity: "resolved",
      journeyState: "suppressed",
      qual: "collecting",
      rep: "not_represented",
      source: "form",
      detail: "STOP recorded on the form channel",
      zone: "San Antonio",
      contact: "suppressed",
      ack: "not_sent",
      consult: "not_ready",
      nurture: "inactive",
      blockers: ["suppressed"],
      inbound: "STOP",
      observations: [],
      stop: true,
    },
    {
      name: "Riley Brooks",
      email: "riley.brooks@example.com",
      identity: "resolved",
      journeyState: "contacted",
      qual: "not_started",
      rep: "unconfirmed",
      source: "form",
      detail: "Acknowledgment commitment blocked — no connector",
      zone: "Austin",
      contact: "contactable",
      ack: "blocked",
      consult: "not_ready",
      nurture: "inactive",
      blockers: ["connector_not_activated"],
      inbound: "Can someone call me about a consult near Mueller?",
      observations: [],
    },
    {
      name: "Ava Nguyen",
      email: "ava.nguyen@example.com",
      identity: "resolved",
      journeyState: "nurture",
      qual: "collecting",
      rep: "not_represented",
      source: "form",
      detail: "2027 relocation — timing not ready",
      zone: "Fredericksburg",
      contact: "contactable",
      ack: "not_sent",
      consult: "not_ready",
      nurture: "active",
      blockers: ["timing_not_ready", "connector_not_activated"],
      inbound: "Job move to Fredericksburg in 2027. Just starting to learn the area. No rush.",
      observations: [
        ["timing", "assertion", "2027 relocation", "form:body"],
        ["geography", "assertion", "Fredericksburg", "form:body"],
        ["purchase_intent", "assertion", "Early research only", "form:body"],
      ],
    },
  ];

  for (const buyer of buyers) {
    const evId = await evidence(`Inbound form artifact for ${buyer.name}`);
    const personId = newId();
    const partyId = newId();
    const journeyId = newId();
    const conversationId = newId();
    const endpoints = [];
    if (buyer.email) {
      endpoints.push({
        endpointId: newId(),
        type: "email",
        normalizedValue: buyer.email.toLowerCase(),
        verificationState: "provider_observed",
        status: buyer.stop ? "suppressed" : "active",
      });
    }
    if (buyer.phone) {
      endpoints.push({
        endpointId: newId(),
        type: "phone",
        normalizedValue: buyer.phone,
        verificationState: "provider_observed",
        status: buyer.stop ? "suppressed" : "active",
      });
    }

    await insertCanonical(
      sql,
      {
        ...canonicalEnvelope({
          id: personId,
          tenantId,
          recordType: "Person",
          status: "active",
          createdBy: actor,
          sourceEvidenceIds: [evId],
          now,
        }),
        identityState: buyer.identity,
        displayName: buyer.name,
        endpoints,
      },
      now,
    );

    await insertCanonical(
      sql,
      {
        ...canonicalEnvelope({
          id: partyId,
          tenantId,
          recordType: "BuyingParty",
          status: "active",
          createdBy: actor,
          sourceEvidenceIds: [evId],
          now,
        }),
        members: [{ personId, role: "buyer" }],
      },
      now,
    );

    await insertCanonical(
      sql,
      {
        ...canonicalEnvelope({
          id: journeyId,
          tenantId,
          recordType: "BuyerJourney",
          status: "active",
          createdBy: actor,
          sourceEvidenceIds: [evId],
          now,
        }),
        buyingPartyId: partyId,
        ownerLicenseHolderId: licenseHolderId,
        journeyState: buyer.journeyState,
        qualificationState: buyer.qual,
        representationState: buyer.rep,
      },
      now,
    );

    await sql`
      insert into journey_ops (
        tenant_id, journey_id, source_channel, source_detail, service_zone,
        contactability, acknowledgment_state, consultation_state, nurture_state, blocker_codes
      ) values (
        ${tenantId}, ${journeyId}, ${buyer.source}, ${buyer.detail}, ${buyer.zone},
        ${buyer.contact}, ${buyer.ack}, ${buyer.consult}, ${buyer.nurture},
        ${JSON.stringify(buyer.blockers)}
      )
    `;

    await sql`
      insert into operational_messages (
        tenant_id, id, journey_id, conversation_id, direction, channel, body, delivery_state, evidence_id, created_at
      ) values (
        ${tenantId}, ${newId()}, ${journeyId}, ${conversationId}, ${"inbound"}, ${buyer.source},
        ${buyer.inbound}, ${"received"}, ${evId}, ${iso(now)}
      )
    `;

    if (!buyer.stop) {
      await insertCanonical(
        sql,
        {
          ...canonicalEnvelope({
            tenantId,
            recordType: "ConsentGrant",
            status: "active",
            createdBy: actor,
            sourceEvidenceIds: [evId],
            now,
          }),
          personId,
          channel: "form",
          purpose: "transactional_inquiry",
          principalId: licenseHolderId,
          basis: "affirmative",
          grantedAt: iso(now),
          presentationEvidenceId: evId,
          validityState: "active",
        },
        now,
      );
    } else {
      await insertCanonical(
        sql,
        {
          ...canonicalEnvelope({
            tenantId,
            recordType: "Suppression",
            status: "active",
            createdBy: actor,
            sourceEvidenceIds: [evId],
            now,
          }),
          subjectId: personId,
          scope: "all_non_required_contact",
          reason: "opt_out",
          suppressedAt: iso(now),
          validityState: "active",
        },
        now,
      );
    }

    for (const [criterion, epi, value] of buyer.observations) {
      const itemId = newId();
      const observationState =
        criterion === "representation" && buyer.rep === "conflict"
          ? "contradicted"
          : epi === "verified_fact"
            ? "verified"
            : epi === "inference"
              ? "inferred"
              : "asserted";
      await insertCanonical(
        sql,
        {
          ...canonicalEnvelope({
            id: itemId,
            tenantId,
            recordType: "EpistemicItem",
            status: "current",
            createdBy: actor,
            sourceEvidenceIds: [evId],
            now,
          }),
          epistemicType: epi,
          proposition: {
            subjectRef: personId,
            predicate: criterion,
            value,
            applicableJourneyId: journeyId,
            validFrom: iso(now),
          },
          speakerOrMethodRef: personId,
          validityState: observationState === "contradicted" ? "contradicted" : "current",
        },
        now,
      );
      await insertCanonical(
        sql,
        {
          ...canonicalEnvelope({
            tenantId,
            recordType: "QualificationObservation",
            status: "current",
            createdBy: actor,
            sourceEvidenceIds: [evId],
            now,
          }),
          journeyId,
          criterionId: criterion,
          epistemicItemId: itemId,
          observationState,
        },
        now,
      );
    }

    if (buyer.identityCase) {
      await sql`
        insert into identity_resolution_cases (
          tenant_id, id, person_id, journey_id, status, detail, created_at
        ) values (
          ${tenantId}, ${newId()}, ${personId}, ${journeyId}, ${"open"}, ${buyer.identityCase}, ${iso(now)}
        )
      `;
    }

    if (buyer.proposedConsult) {
      const start = new Date(now.getTime() + 48 * 3600_000);
      start.setHours(16, 0, 0, 0);
      const end = new Date(start.getTime() + 45 * 60_000);
      await insertCanonical(
        sql,
        {
          ...canonicalEnvelope({
            tenantId,
            recordType: "Appointment",
            status: "active",
            createdBy: actor,
            sourceEvidenceIds: [evId],
            now,
          }),
          journeyId,
          appointmentType: "buyer_consultation",
          participantIds: [personId, licenseHolderId],
          startsAt: start.toISOString(),
          endsAt: end.toISOString(),
          timeZone: "America/Chicago",
          locationOrMode: "proposed_local_policy_slot",
          appointmentState: "proposed",
        },
        now,
      );
    }

    if (buyer.ack === "blocked") {
      await insertCanonical(
        sql,
        {
          ...canonicalEnvelope({
            tenantId,
            recordType: "Commitment",
            status: "active",
            createdBy: actor,
            sourceEvidenceIds: [evId],
            now,
          }),
          journeyId,
          obligorId: licenseHolderId,
          beneficiaryIds: [personId],
          description: "Deterministic acknowledgment is queued. Habitat and the email connector are not activated, so no send was admitted.",
          dueAt: iso(now),
          commitmentState: "blocked",
        },
        now,
      );
    }
  }
}
