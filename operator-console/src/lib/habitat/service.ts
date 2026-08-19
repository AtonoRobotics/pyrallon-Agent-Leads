import type { Sql } from "@/lib/db";
import { ContractViolation } from "@/lib/contracts/violations.ts";
import { iso, newId, sha256Digest, canonicalEnvelope } from "@/lib/canonical/envelope.ts";
import {
  consultationReady,
  mayContact,
  type ConsentRecord,
  type JourneyRecord,
  type ObservationRecord,
  type PersonRecord,
  type SuppressionRecord,
} from "@/lib/canonical/predicates.ts";
import { getRecord, insertCanonical, listByField, listRecords, type StoredRecord } from "@/lib/canonical/store.ts";
import { logEvent } from "@/lib/observability/log.ts";
import { appendEvidence } from "@/lib/evidence/ledger.ts";

export type EffectIntent = {
  intentId?: string;
  tenantId: string;
  actionClass: string;
  payload: Record<string, unknown>;
  idempotencyKey: string;
  journeyId?: string;
  resourceKey: string;
  actorId: string;
  approvalId?: string;
};

export type Permit = {
  permitId: string;
  intentId: string;
  actionClass: string;
  payloadDigest: string;
  permitDigest: string;
  resourceKey: string;
  resourceVersion: string;
  status: string;
  decision: string;
  reasons: string[];
  expiresAt: string;
};

export async function admitIntent(sql: Sql, intent: EffectIntent, now = new Date()): Promise<Permit> {
  if (intent.actionClass === "outbound_ai_voice") {
    throw new ContractViolation([
      {
        code: "GATE_032",
        path: "$.actionClass",
        message: "outbound AI-generated voice is prohibited",
      },
    ]);
  }

  const payloadDigest = sha256Digest(canonicalJson(intent.payload));
  const existing = await sql<{ intent_id: string }>`
    select intent_id from habitat_intents
    where tenant_id = ${intent.tenantId} and idempotency_key = ${intent.idempotencyKey}
  `;
  const intentId = existing[0]?.intent_id ?? intent.intentId ?? newId();
  if (!existing[0]) {
    await sql`
      insert into habitat_intents (
        tenant_id, intent_id, action_class, payload, payload_digest, idempotency_key, journey_id, created_at
      ) values (
        ${intent.tenantId}, ${intentId}, ${intent.actionClass}, ${JSON.stringify(intent.payload)},
        ${payloadDigest}, ${intent.idempotencyKey}, ${intent.journeyId ?? null}, ${iso(now)}
      )
    `;
  } else {
    const prior = await sql<{ payload_digest: string; intent_id: string }>`
      select payload_digest, intent_id from habitat_intents
      where tenant_id = ${intent.tenantId} and intent_id = ${intentId}
    `;
    if (prior[0] && prior[0].payload_digest !== payloadDigest) {
      throw new ContractViolation([
        {
          code: "IDEMPOTENCY_PAYLOAD_MISMATCH",
          path: "$.payload",
          message: "same idempotency key with a different payload",
        },
      ]);
    }
    const issued = await sql<{
      permit_id: string;
      action_class: string;
      payload_digest: string;
      permit_digest: string;
      resource_key: string;
      resource_version: string;
      status: string;
      decision: string;
      reasons: string;
      expires_at: string;
      intent_id: string;
    }>`
      select permit_id, action_class, payload_digest, permit_digest, resource_key, resource_version,
             status, decision, reasons, expires_at, intent_id
      from habitat_permits
      where tenant_id = ${intent.tenantId} and intent_id = ${intentId}
      order by issued_at desc limit 1
    `;
    if (issued[0] && issued[0].status === "issued") return toPermit(issued[0]);
  }

  await acquireLock(sql, intent.tenantId, intent.resourceKey, intentId, now);
  try {
    const evaluation = await evaluate(sql, { ...intent, intentId }, payloadDigest, now);
    const permitId = newId();
    const permitDigest = sha256Digest(
      `${intentId}|${payloadDigest}|${evaluation.resourceVersion}|${permitId}`,
    );
    const expiresAt = new Date(now.getTime() + 120_000).toISOString();
    const status = evaluation.decision === "allow" ? "issued" : "denied";
    await sql`
      insert into habitat_permits (
        tenant_id, permit_id, intent_id, action_class, payload_digest, permit_digest,
        resource_key, resource_version, status, issued_at, expires_at, decision, reasons
      ) values (
        ${intent.tenantId}, ${permitId}, ${intentId}, ${intent.actionClass}, ${payloadDigest},
        ${permitDigest}, ${intent.resourceKey}, ${evaluation.resourceVersion}, ${status},
        ${iso(now)}, ${expiresAt}, ${evaluation.decision}, ${JSON.stringify(evaluation.reasons)}
      )
    `;
    if (evaluation.qualification) {
      await insertCanonical(sql, evaluation.qualification, now);
    }
    await appendEvidence(sql, {
      tenantId: intent.tenantId,
      journeyId: intent.journeyId,
      recordId: permitId,
      payload: {
        kind: "habitat_decision",
        intentId,
        actionClass: intent.actionClass,
        decision: evaluation.decision,
        reasons: evaluation.reasons,
        payloadDigest,
      },
    });
    await logEvent(sql, {
      tenantId: intent.tenantId,
      kind: "habitat_decision",
      journeyId: intent.journeyId,
      payload: { permitId, decision: evaluation.decision, reasons: evaluation.reasons },
      now,
    });
    if (evaluation.decision !== "allow") {
      throw new ContractViolation(
        evaluation.reasons.map((message) => ({
          code: "HABITAT_DENIED",
          path: "$.actionClass",
          message,
        })),
      );
    }
    return {
      permitId,
      intentId,
      actionClass: intent.actionClass,
      payloadDigest,
      permitDigest,
      resourceKey: intent.resourceKey,
      resourceVersion: evaluation.resourceVersion,
      status: "issued",
      decision: "allow",
      reasons: evaluation.reasons,
      expiresAt,
    };
  } finally {
    await sql`
      delete from habitat_locks
      where tenant_id = ${intent.tenantId} and resource_key = ${intent.resourceKey} and intent_id = ${intentId}
    `;
  }
}

export async function redeemPermit(
  sql: Sql,
  tenantId: string,
  permitId: string,
  payloadDigest: string,
  now = new Date(),
) {
  const rows = await sql<{
    status: string;
    payload_digest: string;
    expires_at: string;
    permit_digest: string;
    action_class: string;
    intent_id: string;
  }>`
    select status, payload_digest, expires_at, permit_digest, action_class, intent_id
    from habitat_permits
    where tenant_id = ${tenantId} and permit_id = ${permitId}
  `;
  const row = rows[0];
  if (!row) {
    throw new ContractViolation([
      { code: "PERMIT_NOT_FOUND", path: "$.permitId", message: "unknown permit" },
    ]);
  }
  if (row.status === "redeemed") {
    throw new ContractViolation([
      { code: "PERMIT_REPLAY", path: "$.permitId", message: "single-use permit already redeemed" },
    ]);
  }
  if (row.status !== "issued") {
    throw new ContractViolation([
      { code: "PERMIT_NOT_ISSUED", path: "$.status", message: `permit is ${row.status}` },
    ]);
  }
  if (new Date(row.expires_at).getTime() <= now.getTime()) {
    await sql`
      update habitat_permits set status = ${"expired"}
      where tenant_id = ${tenantId} and permit_id = ${permitId}
    `;
    throw new ContractViolation([
      { code: "PERMIT_EXPIRED", path: "$.expiresAt", message: "permit expired before redeem" },
    ]);
  }
  if (row.payload_digest !== payloadDigest) {
    throw new ContractViolation([
      { code: "PAYLOAD_MUTATION", path: "$.payloadDigest", message: "payload changed after issue" },
    ]);
  }
  const updated = await sql<{ permit_id: string }>`
    update habitat_permits
    set status = ${"redeemed"}, redeemed_at = ${iso(now)}
    where tenant_id = ${tenantId} and permit_id = ${permitId} and status = ${"issued"}
    returning permit_id
  `;
  if (!updated[0]) {
    throw new ContractViolation([
      { code: "PERMIT_REPLAY", path: "$.permitId", message: "concurrent redeem lost" },
    ]);
  }
  await logEvent(sql, {
    tenantId,
    kind: "permit_redeemed",
    payload: { permitId, actionClass: row.action_class },
    now,
  });
  return row.permit_digest;
}

async function acquireLock(
  sql: Sql,
  tenantId: string,
  resourceKey: string,
  intentId: string,
  now: Date,
) {
  const expires = new Date(now.getTime() + 30_000).toISOString();
  await sql`delete from habitat_locks where tenant_id = ${tenantId} and expires_at < ${iso(now)}`;
  try {
    await sql`
      insert into habitat_locks (tenant_id, resource_key, intent_id, acquired_at, expires_at)
      values (${tenantId}, ${resourceKey}, ${intentId}, ${iso(now)}, ${expires})
    `;
  } catch {
    throw new ContractViolation([
      {
        code: "RESOURCE_LOCKED",
        path: "$.resourceKey",
        message: "another intent holds this resource",
      },
    ]);
  }
}

async function evaluate(
  sql: Sql,
  intent: EffectIntent & { intentId: string },
  payloadDigest: string,
  now: Date,
) {
  const reasons: string[] = [];
  const journeyId = String(intent.payload.journeyId ?? intent.journeyId ?? "");
  let resourceVersion = "0";
  let qualification: Record<string, unknown> | undefined;

  if (intent.approvalId) {
    const approval = await getRecord<StoredRecord & { decision?: string; expiresAt?: string; digest?: string }>(
      sql,
      intent.tenantId,
      intent.approvalId,
    );
    if (!approval) reasons.push("approval_missing");
    else {
      if (approval.decision !== "approved") reasons.push("approval_not_granted");
      if (approval.expiresAt && new Date(String(approval.expiresAt)).getTime() <= now.getTime()) {
        reasons.push("approval_expired");
      }
      if (approval.digest && approval.digest !== payloadDigest) reasons.push("approval_payload_mismatch");
    }
  }

  if (journeyId) {
    const journey = await getRecord<JourneyRecord>(sql, intent.tenantId, journeyId);
    if (journey) resourceVersion = String(journey.version);
    if (
      intent.actionClass.startsWith("outbound") &&
      journey
    ) {
      const party = await getRecord<StoredRecord & { members: Array<{ personId: string }> }>(
        sql,
        intent.tenantId,
        journey.buyingPartyId,
      );
      const person = party
        ? await getRecord<PersonRecord>(sql, intent.tenantId, party.members[0]?.personId ?? "")
        : null;
      if (person) {
        const consents = await listByField<ConsentRecord>(
          sql,
          intent.tenantId,
          "ConsentGrant",
          "personId",
          person.id,
        );
        const suppressions = await listByField<SuppressionRecord>(
          sql,
          intent.tenantId,
          "Suppression",
          "subjectId",
          person.id,
        );
        const channel = intent.actionClass === "outbound_sms" ? "sms" : "email";
        const contact = mayContact({
          person,
          consents,
          suppressions,
          channel,
          purpose: String(intent.payload.purpose ?? "transactional_inquiry"),
        });
        if (!contact.allowed) reasons.push(...contact.reasons);
      }
    }
    if (
      (intent.actionClass === "calendar_write" || intent.actionClass === "propose_local_slot") &&
      journey
    ) {
      const party = await getRecord<StoredRecord & { members: Array<{ personId: string }> }>(
        sql,
        intent.tenantId,
        journey.buyingPartyId,
      );
      const person = party
        ? await getRecord<PersonRecord>(sql, intent.tenantId, party.members[0]?.personId ?? "")
        : null;
      const observations = await listByField<ObservationRecord>(
        sql,
        intent.tenantId,
        "QualificationObservation",
        "journeyId",
        journeyId,
      );
      const openCases = await sql<{ id: string }>`
        select id from identity_resolution_cases
        where tenant_id = ${intent.tenantId} and journey_id = ${journeyId} and status = ${"open"}
      `;
      if (person) {
        const ready = consultationReady({
          person,
          journey,
          observations,
          openIdentityCase: openCases.length > 0,
        });
        if (!ready.ready) reasons.push(...ready.reasons);
      }
    }
    if (
      intent.actionClass === "residential_showing" ||
      intent.actionClass === "residential_offer_presentation"
    ) {
      const result = await qualifyAgreement(sql, intent, payloadDigest, now);
      qualification = result.record;
      if (result.record.result !== "qualified") {
        reasons.push(...(result.record.reasons as string[]));
      }
    }
  } else if (
    intent.actionClass === "residential_showing" ||
    intent.actionClass === "residential_offer_presentation"
  ) {
    const result = await qualifyAgreement(sql, intent, payloadDigest, now);
    qualification = result.record;
    if (result.record.result !== "qualified") {
      reasons.push(...(result.record.reasons as string[]));
    }
  }

  return {
    decision: reasons.length ? "deny" : "allow",
    reasons: reasons.length ? reasons : ["admitted"],
    resourceVersion,
    qualification,
  };
}

async function qualifyAgreement(
  sql: Sql,
  intent: EffectIntent & { intentId: string },
  payloadDigest: string,
  now: Date,
) {
  const reasons: string[] = [];
  const actionType = intent.actionClass as "residential_showing" | "residential_offer_presentation";
  const buyerPartyId = String(intent.payload.buyerPartyId ?? "");
  const brokerageId = String(intent.payload.brokerageId ?? "");
  const licenseHolderId = String(intent.payload.responsibleLicenseHolderId ?? "");
  const propertyReferenceId = String(intent.payload.propertyReferenceId ?? "");
  const exceptionCode = intent.payload.exceptionCode
    ? String(intent.payload.exceptionCode)
    : undefined;

  const agreements = (await listRecords<StoredRecord>(sql, intent.tenantId, "WrittenBuyerAgreement")) as Array<
    StoredRecord & {
      agreementType: string;
      executionState: string;
      buyerPartyIds: string[];
      brokerPartyId: string;
      terminatesAt: string;
      effectiveAt: string;
      serviceDefinitions: Array<{ serviceCode: string; allowed: boolean }>;
    }
  >;
  const covering = agreements.filter(
    (a) =>
      a.executionState === "effective" &&
      a.buyerPartyIds?.includes(buyerPartyId) &&
      a.brokerPartyId === brokerageId &&
      new Date(a.effectiveAt).getTime() <= now.getTime() &&
      new Date(a.terminatesAt).getTime() > now.getTime(),
  );

  if (covering.length > 1) reasons.push("agreement_conflict");

  let chosen = covering[0];
  if (exceptionCode === "listing_brokerage_open_house") {
    reasons.push("open_house_exception_requires_canonical_listing_evidence");
  }

  if (!chosen && !exceptionCode) reasons.push("no_effective_agreement");

  if (chosen && actionType === "residential_showing") {
    const ok =
      chosen.agreementType === "representation" ||
      chosen.agreementType === "non_representation_showing";
    if (!ok) reasons.push("agreement_does_not_cover_showing");
  }
  if (chosen && actionType === "residential_offer_presentation") {
    if (chosen.agreementType !== "representation") {
      reasons.push("showing_only_cannot_qualify_offer");
    } else {
      const offer = chosen.serviceDefinitions?.some(
        (s) => s.serviceCode === "offer_presentation" && s.allowed,
      );
      if (!offer) reasons.push("representation_missing_offer_service");
    }
  }

  const iabs = await listRecords<
    StoredRecord & { recipientPersonId: string; validityState: string; trigger: string }
  >(sql, intent.tenantId, "IabsDelivery");
  const party = buyerPartyId
    ? await getRecord<StoredRecord & { members: Array<{ personId: string }> }>(
        sql,
        intent.tenantId,
        buyerPartyId,
      )
    : null;
  const memberIds = new Set((party?.members ?? []).map((m) => m.personId));
  const delivered = iabs.find(
    (d) => d.validityState === "delivered" && (memberIds.size === 0 || memberIds.has(d.recipientPersonId)),
  );
  if (!delivered && !exceptionCode) reasons.push("iabs_not_delivered");

  const result = reasons.length ? "denied" : "qualified";
  const record: Record<string, unknown> = {
    ...canonicalEnvelope({
      tenantId: intent.tenantId,
      recordType: "AgreementQualification",
      status: "current",
      createdBy: { actorType: "service_principal", actorId: "habitat" },
      sourceEvidenceIds: [intent.intentId],
      now,
    }),
    actionType,
    actionIntentId: intent.intentId,
    actionPayloadDigest: payloadDigest,
    buyerPartyId,
    responsibleLicenseHolderId: licenseHolderId,
    brokerageId,
    propertyReferenceId,
    evaluatedAt: iso(now),
    policyVersion: "broker-policy/0.1.0",
    result,
    reasons: reasons.length ? reasons : ["qualified"],
    expiresAt: new Date(now.getTime() + 120_000).toISOString(),
  };
  if (chosen) {
    record.agreementId = chosen.id;
    record.agreementVersion = chosen.version;
  }
  if (delivered) record.iabsDeliveryId = delivered.id;
  if (exceptionCode) record.exceptionCode = exceptionCode;
  return { record };
}

function toPermit(row: {
  permit_id: string;
  intent_id: string;
  action_class: string;
  payload_digest: string;
  permit_digest: string;
  resource_key: string;
  resource_version: string;
  status: string;
  decision: string;
  reasons: string;
  expires_at: string;
}): Permit {
  return {
    permitId: row.permit_id,
    intentId: row.intent_id,
    actionClass: row.action_class,
    payloadDigest: row.payload_digest,
    permitDigest: row.permit_digest,
    resourceKey: row.resource_key,
    resourceVersion: row.resource_version,
    status: row.status,
    decision: row.decision,
    reasons: JSON.parse(row.reasons) as string[],
    expiresAt: row.expires_at,
  };
}

export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map((v) => canonicalJson(v)).join(",")}]`;
  const obj = value as Record<string, unknown>;
  const keys = Object.keys(obj).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(obj[k])}`).join(",")}}`;
}
