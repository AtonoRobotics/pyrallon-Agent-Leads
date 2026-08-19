import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { openPacketSql } from "../test/pg.ts";
import {
  NOW,
  createBuyer,
  createIabs,
  createProperty,
  createRepresentationAgreement,
  createShowingAgreement,
  createTenant,
  formEnvelope,
} from "../test/fixtures.ts";
import { ActivationBlocked, ContractViolation } from "../contracts/violations.ts";
import { listRecords, updateCanonical, getRecord } from "../canonical/store.ts";
import { reconstructTenant, assertTenantIsolation } from "../canonical/reconstruct.ts";
import { appendEvidence, verifyChain, tombstoneEvidence, listLiveEvidence } from "../evidence/ledger.ts";
import { admitIntent, redeemPermit, canonicalJson } from "../habitat/service.ts";
import { invokeProvider, inventory } from "../connectors/gateway.ts";
import { admitInbound } from "../ingress/admit.ts";
import {
  fold,
  replay,
  signalWorkflow,
  scheduleTimer,
  fireDueTimers,
} from "../workflow/runtime.ts";
import {
  applyProposal,
  compileContext,
  compileWorkRequest,
  deterministicQualificationProposal,
  validateWorkPair,
  validateProposal,
  assertLiveCognitionActivated,
} from "../cognition/compiler.ts";
import { localPolicySlots, persistSlotSet } from "../slots/policy.ts";
import { PACKETS, ACTIVATION } from "./registry.ts";
import { recordDefaultGates, listGates } from "./gates.ts";
import { sha256Digest } from "../canonical/envelope.ts";
import type { Sql } from "../db.ts";
import type { PersonRecord } from "../canonical/predicates.ts";

function isCode(err: unknown, code: string) {
  if (err instanceof ContractViolation) return err.violations.some((v) => v.code === code);
  if (err instanceof ActivationBlocked) return err.code === code;
  return false;
}

async function withSql(run: (sql: Sql) => Promise<void>) {
  const { sql, close } = await openPacketSql();
  try {
    await run(sql);
  } finally {
    await close();
  }
}

describe("PKT-01 canonical CRM", () => {
  it("isolates tenants and reconstructs without collapse", async () => {
    await withSql(async (sql) => {
      const a = await createTenant(sql, "tenant-a");
      const b = await createTenant(sql, "tenant-b");
      await createBuyer(sql, {
        tenantId: a.tenantId,
        licenseHolderId: a.licenseHolderId,
        name: "Elena Vasquez",
        email: "elena.a@example.com",
      });
      await createBuyer(sql, {
        tenantId: b.tenantId,
        licenseHolderId: b.licenseHolderId,
        name: "Marcus Hale",
        email: "marcus.b@example.com",
      });
      const reconA = await reconstructTenant(sql, a.tenantId);
      const reconB = await reconstructTenant(sql, b.tenantId);
      assert.equal(reconA.participantCount, 1);
      assert.equal(reconB.participantCount, 1);
      assert.equal(reconA.collapsed, false);
      const isolation = await assertTenantIsolation(sql, a.tenantId, b.tenantId);
      assert.equal(isolation.leak, false);
    });
  });

  it("rejects optimistic version conflicts", async () => {
    await withSql(async (sql) => {
      const t = await createTenant(sql, "tenant-v");
      const { person } = await createBuyer(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        name: "Versioned",
        email: "version@example.com",
      });
      const current = (await getRecord<PersonRecord>(sql, t.tenantId, person.id))!;
      await updateCanonical(sql, t.tenantId, current, { displayName: "First" }, NOW);
      await assert.rejects(
        () => updateCanonical(sql, t.tenantId, current, { displayName: "Second" }, NOW),
        (err: unknown) => isCode(err, "VERSION_CONFLICT"),
      );
    });
  });

  it("enforces endpoint uniqueness per tenant", async () => {
    await withSql(async (sql) => {
      const t = await createTenant(sql, "tenant-ep");
      await createBuyer(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        name: "One",
        email: "same@example.com",
      });
      await assert.rejects(() =>
        createBuyer(sql, {
          tenantId: t.tenantId,
          licenseHolderId: t.licenseHolderId,
          name: "Two",
          email: "same@example.com",
        }),
      );
    });
  });
});

describe("PKT-02 evidence ledger", () => {
  it("chains, checkpoints, tombstones, and detects tamper", async () => {
    await withSql(async (sql) => {
      const tenantId = "tenant-ev";
      await createTenant(sql, tenantId);
      for (let i = 0; i < 11; i += 1) {
        await appendEvidence(sql, { tenantId, payload: { n: i } });
      }
      const ok = await verifyChain(sql, tenantId);
      assert.equal(ok.entries, 11);
      const checkpoints = await sql<{ seq: number }>`
        select seq from evidence_checkpoints where tenant_id = ${tenantId}
      `;
      assert.equal(checkpoints.length, 1);
      const live = await listLiveEvidence(sql, tenantId);
      await tombstoneEvidence(sql, tenantId, live[0].id, NOW);
      const after = await listLiveEvidence(sql, tenantId);
      assert.equal(after.length, live.length - 1);

      await sql`
        update evidence_ledger set payload = ${JSON.stringify({ n: 99 })}
        where tenant_id = ${tenantId} and seq = 3
      `;
      await assert.rejects(() => verifyChain(sql, tenantId), (err: unknown) => isCode(err, "EVIDENCE_TAMPER"));
    });
  });
});

describe("PKT-03 Habitat", () => {
  it("rejects GATE-032 voice before issue", async () => {
    await withSql(async (sql) => {
      const t = await createTenant(sql, "tenant-voice");
      await assert.rejects(
        () =>
          admitIntent(sql, {
            tenantId: t.tenantId,
            actionClass: "outbound_ai_voice",
            payload: {},
            idempotencyKey: "voice-1",
            resourceKey: "voice",
            actorId: t.licenseHolderId,
          }),
        (err: unknown) => isCode(err, "GATE_032"),
      );
    });
  });

  it("issues a single-use permit and rejects replay and payload mutation", async () => {
    await withSql(async (sql) => {
      const t = await createTenant(sql, "tenant-permit");
      const buyer = await createBuyer(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        name: "Contactable",
        email: "c@example.com",
      });
      const payload = { journeyId: buyer.journey.id, purpose: "transactional_inquiry" };
      const permit = await admitIntent(sql, {
        tenantId: t.tenantId,
        actionClass: "outbound_email",
        payload,
        idempotencyKey: "ack-1",
        resourceKey: `journey:${buyer.journey.id}:ack`,
        actorId: t.licenseHolderId,
        journeyId: String(buyer.journey.id),
      }, NOW);
      assert.equal(permit.decision, "allow");
      await redeemPermit(sql, t.tenantId, permit.permitId, permit.payloadDigest, NOW);
      await assert.rejects(
        () => redeemPermit(sql, t.tenantId, permit.permitId, permit.payloadDigest, NOW),
        (err: unknown) => isCode(err, "PERMIT_REPLAY"),
      );
      const permit2 = await admitIntent(sql, {
        tenantId: t.tenantId,
        actionClass: "outbound_email",
        payload,
        idempotencyKey: "ack-2",
        resourceKey: `journey:${buyer.journey.id}:ack2`,
        actorId: t.licenseHolderId,
        journeyId: String(buyer.journey.id),
      }, NOW);
      await assert.rejects(
        () => redeemPermit(sql, t.tenantId, permit2.permitId, sha256Digest("mutated"), NOW),
        (err: unknown) => isCode(err, "PAYLOAD_MUTATION"),
      );
    });
  });

  it("lets suppression defeat outbound", async () => {
    await withSql(async (sql) => {
      const t = await createTenant(sql, "tenant-stop");
      const buyer = await createBuyer(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        name: "Stopped",
        email: "stop@example.com",
        stop: true,
      });
      await assert.rejects(
        () =>
          admitIntent(sql, {
            tenantId: t.tenantId,
            actionClass: "outbound_email",
            payload: { journeyId: buyer.journey.id, purpose: "transactional_inquiry" },
            idempotencyKey: "stop-ack",
            resourceKey: `journey:${buyer.journey.id}:ack`,
            actorId: t.licenseHolderId,
            journeyId: String(buyer.journey.id),
          }),
        (err: unknown) =>
          err instanceof ContractViolation && err.violations.some((v) => v.message.includes("suppressed")),
      );
    });
  });

  it("qualifies a showing-only agreement for showing and refuses it for offer", async () => {
    await withSql(async (sql) => {
      const t = await createTenant(sql, "tenant-show");
      const buyer = await createBuyer(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        name: "Show",
        email: "show@example.com",
      });
      const property = await createProperty(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        address: "100 Alamo Plaza, San Antonio, TX",
      });
      await createIabs(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        brokerageId: t.brokerageId,
        personId: buyer.person.id,
      });
      await createShowingAgreement(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        brokerageId: t.brokerageId,
        buyerPartyId: buyer.party.id,
      });
      const payload = {
        journeyId: buyer.journey.id,
        buyerPartyId: buyer.party.id,
        brokerageId: t.brokerageId,
        responsibleLicenseHolderId: t.licenseHolderId,
        propertyReferenceId: property.id,
      };
      const showing = await admitIntent(
        sql,
        {
          tenantId: t.tenantId,
          actionClass: "residential_showing",
          payload,
          idempotencyKey: "show-1",
          resourceKey: `showing:${property.id}`,
          actorId: t.licenseHolderId,
          journeyId: String(buyer.journey.id),
        },
        NOW,
      );
      assert.equal(showing.decision, "allow");
      await assert.rejects(
        () =>
          admitIntent(
            sql,
            {
              tenantId: t.tenantId,
              actionClass: "residential_offer_presentation",
              payload,
              idempotencyKey: "offer-1",
              resourceKey: `offer:${property.id}`,
              actorId: t.licenseHolderId,
              journeyId: String(buyer.journey.id),
            },
            NOW,
          ),
        (err: unknown) =>
          err instanceof ContractViolation &&
          err.violations.some((v) => v.message.includes("showing_only_cannot_qualify_offer")),
      );
    });
  });

  it("qualifies offer presentation only on a representation agreement", async () => {
    await withSql(async (sql) => {
      const t = await createTenant(sql, "tenant-offer");
      const buyer = await createBuyer(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        name: "Offer",
        email: "offer@example.com",
      });
      const property = await createProperty(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        address: "200 Congress Ave, Austin, TX",
      });
      await createIabs(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        brokerageId: t.brokerageId,
        personId: buyer.person.id,
      });
      await createRepresentationAgreement(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        brokerageId: t.brokerageId,
        buyerPartyId: buyer.party.id,
      });
      const permit = await admitIntent(
        sql,
        {
          tenantId: t.tenantId,
          actionClass: "residential_offer_presentation",
          payload: {
            journeyId: buyer.journey.id,
            buyerPartyId: buyer.party.id,
            brokerageId: t.brokerageId,
            responsibleLicenseHolderId: t.licenseHolderId,
            propertyReferenceId: property.id,
          },
          idempotencyKey: "offer-ok",
          resourceKey: `offer:${property.id}`,
          actorId: t.licenseHolderId,
          journeyId: String(buyer.journey.id),
        },
        NOW,
      );
      assert.equal(permit.decision, "allow");
      const quals = await listRecords(sql, t.tenantId, "AgreementQualification");
      assert.equal(quals.length, 1);
      assert.equal((quals[0] as unknown as { result: string }).result, "qualified");
    });
  });

  it("rejects a showing without IABS", async () => {
    await withSql(async (sql) => {
      const t = await createTenant(sql, "tenant-iabs");
      const buyer = await createBuyer(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        name: "NoIabs",
        email: "noiabs@example.com",
      });
      const property = await createProperty(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        address: "1 Main St",
      });
      await createShowingAgreement(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        brokerageId: t.brokerageId,
        buyerPartyId: buyer.party.id,
      });
      await assert.rejects(
        () =>
          admitIntent(
            sql,
            {
              tenantId: t.tenantId,
              actionClass: "residential_showing",
              payload: {
                journeyId: buyer.journey.id,
                buyerPartyId: buyer.party.id,
                brokerageId: t.brokerageId,
                responsibleLicenseHolderId: t.licenseHolderId,
                propertyReferenceId: property.id,
              },
              idempotencyKey: "no-iabs",
              resourceKey: "showing:no-iabs",
              actorId: t.licenseHolderId,
              journeyId: String(buyer.journey.id),
            },
            NOW,
          ),
        (err: unknown) =>
          err instanceof ContractViolation &&
          err.violations.some((v) => v.message.includes("iabs_not_delivered")),
      );
    });
  });
});

describe("PKT-04 in-process workflow", () => {
  it("replays history, ignores duplicate signals, and fires timers", async () => {
    await withSql(async (sql) => {
      const t = await createTenant(sql, "tenant-wf");
      const buyer = await createBuyer(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        name: "Flow",
        email: "flow@example.com",
      });
      const workflowId = `buyer-journey:${buyer.journey.id}`;
      const first = await signalWorkflow(sql, {
        tenantId: t.tenantId,
        workflowId,
        eventType: "QualificationUpdated",
        eventId: "q-1",
        payload: { qualificationState: "collecting" },
        now: NOW,
      });
      assert.equal(first.duplicate, false);
      const dup = await signalWorkflow(sql, {
        tenantId: t.tenantId,
        workflowId,
        eventType: "QualificationUpdated",
        eventId: "q-1",
        payload: { qualificationState: "collecting" },
        now: NOW,
      });
      assert.equal(dup.duplicate, true);
      const replayed = await replay(sql, t.tenantId, workflowId);
      const history = await sql<{ event_type: string; payload: string }>`
        select event_type, payload from workflow_history
        where tenant_id = ${t.tenantId} and workflow_id = ${workflowId}
        order by event_seq
      `;
      const folded = fold(
        history.map((r) => ({ eventType: r.event_type, payload: JSON.parse(r.payload) })),
      );
      assert.deepEqual(replayed, folded);
      await scheduleTimer(sql, {
        tenantId: t.tenantId,
        workflowId,
        timerId: "nurture-1",
        fireAt: new Date(NOW.getTime() - 1000),
      });
      const fired = await fireDueTimers(sql, t.tenantId, NOW);
      assert.deepEqual(fired, ["nurture-1"]);
      const after = await replay(sql, t.tenantId, workflowId);
      assert.ok(after.blockerCodes.includes("timer_fired"));
    });
  });

  it("does not claim Temporal Cloud", () => {
    const pkt = PACKETS.find((p) => p.id === "PKT-04");
    assert.equal(pkt?.status, "complete_in_process");
    assert.equal(ACTIVATION.temporal, "not_temporal_cloud");
    assert.match(pkt!.summary, /not Temporal Cloud/i);
  });
});

describe("PKT-05 form ingress", () => {
  it("admits a form, is idempotent, records STOP, and denies email without a grant", async () => {
    await withSql(async (sql) => {
      const t = await createTenant(sql, "tenant-in");
      const envelope = formEnvelope({ email: "ingress@example.com", fullName: "In Person" });
      const first = await admitInbound(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        envelope,
        now: NOW,
      });
      assert.equal(first.outcome, "created");
      const second = await admitInbound(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        envelope,
        now: NOW,
      });
      assert.equal(second.outcome, "duplicate");
      assert.equal(second.journeyId, first.journeyId);

      const stop = await admitInbound(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        envelope: formEnvelope({
          providerEventId: "stop-1",
          email: "stop.ingress@example.com",
          fullName: "Stop Me",
          body: "STOP",
        }),
        now: NOW,
      });
      assert.equal(stop.stop, true);
      const suppressions = await listRecords(sql, t.tenantId, "Suppression");
      assert.ok(suppressions.length >= 1);

      await assert.rejects(
        () =>
          admitInbound(sql, {
            tenantId: t.tenantId,
            licenseHolderId: t.licenseHolderId,
            envelope: {
              ...formEnvelope({ email: "mail@example.com" }),
              channel: "email",
              signatureVerification: "verified",
              providerAccountRef: "email.inbound",
            },
            now: NOW,
          }),
        (err: unknown) => isCode(err, "CONNECTOR_INACTIVE"),
      );
    });
  });
});

describe("PKT-06 connectors", () => {
  it("redeems then fail-closes on an inactive email grant", async () => {
    await withSql(async (sql) => {
      const t = await createTenant(sql, "tenant-conn");
      const buyer = await createBuyer(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        name: "Send",
        email: "send@example.com",
      });
      const payload = { journeyId: buyer.journey.id, purpose: "transactional_inquiry" };
      const permit = await admitIntent(sql, {
        tenantId: t.tenantId,
        actionClass: "outbound_email",
        payload,
        idempotencyKey: "send-1",
        resourceKey: `journey:${buyer.journey.id}:send`,
        actorId: t.licenseHolderId,
        journeyId: String(buyer.journey.id),
      }, NOW);
      await assert.rejects(
        () =>
          invokeProvider({
            sql,
            tenantId: t.tenantId,
            permit,
            connectorId: "email.outbound",
            payload,
            licenseHolderId: t.licenseHolderId,
            now: NOW,
          }),
        (err: unknown) => isCode(err, "CONNECTOR_INACTIVE"),
      );
      const attempts = await listRecords(sql, t.tenantId, "EffectAttempt");
      assert.equal(attempts.length, 1);
      assert.equal((attempts[0] as unknown as { attemptState: string }).attemptState, "rejected");
      await assert.rejects(
        () => redeemPermit(sql, t.tenantId, permit.permitId, permit.payloadDigest, NOW),
        (err: unknown) => isCode(err, "PERMIT_REPLAY"),
      );
      const voice = inventory().find((c) => c.channel === "voice");
      assert.equal(voice?.status, "prohibited");
    });
  });
});

describe("PKT-07 and PKT-08 cognition", () => {
  it("compiles a valid request/proposal pair and applies assertions only", async () => {
    await withSql(async (sql) => {
      const t = await createTenant(sql, "tenant-cog");
      const buyer = await createBuyer(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        name: "Cog",
        email: "cog@example.com",
      });
      const compiled = await compileContext(sql, {
        tenantId: t.tenantId,
        journeyId: String(buyer.journey.id),
        now: NOW,
      });
      const request = compileWorkRequest({
        tenantId: t.tenantId,
        principalId: t.licenseHolderId,
        journeyId: String(buyer.journey.id),
        compiled,
        now: NOW,
      });
      const proposal = deterministicQualificationProposal({
        workId: request.workId,
        contextManifestId: compiled.manifestId,
        personId: buyer.person.id,
        journeyId: String(buyer.journey.id),
        message: "Relocating to San Antonio this fall. Budget around $850k.",
        sourceIds: compiled.sourceRecordIds,
        now: NOW,
      });
      validateWorkPair(request, proposal);
      const applied = await applyProposal(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        journeyId: String(buyer.journey.id),
        personId: buyer.person.id,
        proposal,
        now: NOW,
      });
      assert.ok(applied.written.includes("purchase_intent"));
      assert.ok(applied.written.includes("budget_financing"));
      const items = await listRecords(sql, t.tenantId, "EpistemicItem");
      assert.ok(
        items.every((i) => {
          const rec = i as unknown as { epistemicType: string; speakerOrMethodRef?: string };
          return rec.epistemicType !== "verified_fact" || rec.speakerOrMethodRef !== "model:grok";
        }),
      );

      const bad = structuredClone(proposal);
      (bad.proposedActions as Array<{ actionClass: string }>)[0].actionClass = "outbound_email";
      assert.throws(() => validateProposal(bad), (err: unknown) => isCode(err, "PROHIBITED_COGNITIVE_ACTION"));

      const fact = structuredClone(proposal);
      (fact.claims as Array<{ epistemicType: string }>)[0].epistemicType = "verified_fact";
      assert.throws(() => validateProposal(fact), (err: unknown) => isCode(err, "ILLEGAL_MODEL_FACT"));

      assert.throws(
        () =>
          deterministicQualificationProposal({
            workId: request.workId,
            contextManifestId: compiled.manifestId,
            personId: buyer.person.id,
            journeyId: String(buyer.journey.id),
            message: "Looking for a neighborhood without children",
            sourceIds: compiled.sourceRecordIds,
            now: NOW,
          }),
        (err: unknown) => isCode(err, "PROHIBITED_PROXY"),
      );

      const previous = process.env.XAI_API_KEY;
      delete process.env.XAI_API_KEY;
      try {
        assert.throws(() => assertLiveCognitionActivated(), (err: unknown) => isCode(err, "COGNITION_NOT_ACTIVATED"));
      } finally {
        if (previous !== undefined) process.env.XAI_API_KEY = previous;
      }
    });
  });
});

describe("PKT-09 local slots", () => {
  it("produces local policy slots and refuses calendar confirm", async () => {
    await withSql(async (sql) => {
      const t = await createTenant(sql, "tenant-slot");
      const buyer = await createBuyer(sql, {
        tenantId: t.tenantId,
        licenseHolderId: t.licenseHolderId,
        name: "Slot",
        email: "slot@example.com",
        consultReady: true,
      });
      const slots = localPolicySlots(NOW);
      assert.ok(slots.length >= 4);
      assert.ok(slots.every((s) => s.timeZone === "America/Chicago"));
      const set = await persistSlotSet(sql, {
        tenantId: t.tenantId,
        journeyId: String(buyer.journey.id),
        slots,
        now: NOW,
      });
      const permit = await admitIntent(
        sql,
        {
          tenantId: t.tenantId,
          actionClass: "propose_local_slot",
          payload: { journeyId: buyer.journey.id, startsAt: set.slots[0].startsAt },
          idempotencyKey: "slot-1",
          resourceKey: `slot:${buyer.journey.id}`,
          actorId: t.licenseHolderId,
          journeyId: String(buyer.journey.id),
        },
        NOW,
      );
      assert.equal(permit.decision, "allow");
      const confirm = await admitIntent(
        sql,
        {
          tenantId: t.tenantId,
          actionClass: "calendar_write",
          payload: { journeyId: buyer.journey.id, startsAt: set.slots[0].startsAt },
          idempotencyKey: "cal-1",
          resourceKey: `cal:${buyer.journey.id}`,
          actorId: t.licenseHolderId,
          journeyId: String(buyer.journey.id),
        },
        NOW,
      );
      await assert.rejects(
        () =>
          invokeProvider({
            sql,
            tenantId: t.tenantId,
            permit: confirm,
            connectorId: "calendar.write",
            payload: { journeyId: buyer.journey.id },
            licenseHolderId: t.licenseHolderId,
            now: NOW,
          }),
        (err: unknown) => isCode(err, "CONNECTOR_INACTIVE"),
      );
    });
  });
});

describe("PKT-10 operator honesty", () => {
  it("lists every packet and records fail-closed gates", async () => {
    await withSql(async (sql) => {
      await recordDefaultGates(sql, NOW);
      const gates = await listGates(sql);
      assert.equal(PACKETS.length, 11);
      assert.equal(ACTIVATION.habitat, "in_process");
      assert.equal(ACTIVATION.connectors.email, "inactive");
      assert.equal(ACTIVATION.connectors.voice, "prohibited");
      const temporal = gates.find((g) => g.gateId === "TEMPORAL-CLOUD");
      assert.equal(temporal?.status, "blocked");
      const voice = gates.find((g) => g.gateId === "GATE-032");
      assert.equal(voice?.status, "pass");
    });
  });
});

describe("canonical JSON", () => {
  it("is key-order independent", () => {
    assert.equal(canonicalJson({ b: 1, a: 2 }), canonicalJson({ a: 2, b: 1 }));
  });
});
