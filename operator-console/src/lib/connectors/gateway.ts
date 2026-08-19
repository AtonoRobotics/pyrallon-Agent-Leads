import type { Sql } from "@/lib/db";
import { ActivationBlocked, ContractViolation } from "@/lib/contracts/violations.ts";
import { canonicalEnvelope, iso, newId } from "@/lib/canonical/envelope.ts";
import { redeemPermit, type Permit } from "@/lib/habitat/service.ts";
import { logEvent } from "@/lib/observability/log.ts";
import { insertCanonical } from "@/lib/canonical/store.ts";

export const CONNECTOR_INVENTORY = [
  {
    connectorId: "form.local",
    channel: "form",
    status: "active" as const,
    scopes: ["ingress"],
    sideEffect: "none",
    notes: "This app is the branded form.",
  },
  {
    connectorId: "email.outbound",
    channel: "email",
    status: "inactive" as const,
    scopes: ["send"],
    sideEffect: "provider_write",
    notes: "No live email provider identity is configured.",
  },
  {
    connectorId: "sms.outbound",
    channel: "sms",
    status: "inactive" as const,
    scopes: ["send"],
    sideEffect: "provider_write",
    notes: "No live SMS provider identity is configured.",
  },
  {
    connectorId: "calendar.write",
    channel: "calendar",
    status: "inactive" as const,
    scopes: ["read", "write"],
    sideEffect: "provider_write",
    notes: "No live calendar OAuth grant is configured.",
  },
  {
    connectorId: "voice.outbound",
    channel: "voice",
    status: "prohibited" as const,
    scopes: [],
    sideEffect: "prohibited",
    notes: "GATE-032: outbound AI voice is prohibited.",
  },
] as const;

export function inventory() {
  return CONNECTOR_INVENTORY;
}

export async function ensureDefaultGrants(sql: Sql, tenantId: string) {
  const existing = await sql<{ connector_id: string }>`
    select connector_id from connector_grants where tenant_id = ${tenantId}
  `;
  const have = new Set(existing.map((r) => r.connector_id));
  const now = iso();
  for (const item of CONNECTOR_INVENTORY) {
    if (have.has(item.connectorId)) continue;
    await sql`
      insert into connector_grants (
        tenant_id, connector_id, channel, status, scopes, created_at
      ) values (
        ${tenantId}, ${item.connectorId}, ${item.channel}, ${item.status},
        ${JSON.stringify(item.scopes)}, ${now}
      )
    `;
  }
}

export async function grantStatus(sql: Sql, tenantId: string, connectorId: string) {
  const rows = await sql<{ status: string; channel: string; revoked_at: string | null }>`
    select status, channel, revoked_at from connector_grants
    where tenant_id = ${tenantId} and connector_id = ${connectorId}
  `;
  return rows[0] ?? null;
}

export async function revokeGrant(sql: Sql, tenantId: string, connectorId: string, now = new Date()) {
  const rows = await sql<{ connector_id: string }>`
    update connector_grants
    set status = ${"revoked"}, revoked_at = ${now.toISOString()}
    where tenant_id = ${tenantId} and connector_id = ${connectorId}
    returning connector_id
  `;
  if (!rows[0]) {
    throw new ContractViolation([
      { code: "GRANT_NOT_FOUND", path: "$.connectorId", message: "unknown grant" },
    ]);
  }
  await logEvent(sql, {
    tenantId,
    kind: "connector_revoked",
    payload: { connectorId },
    now,
  });
}

export async function invokeProvider(input: {
  sql: Sql;
  tenantId: string;
  permit: Permit;
  connectorId: string;
  payload: Record<string, unknown>;
  licenseHolderId: string;
  now?: Date;
}) {
  const spec = CONNECTOR_INVENTORY.find((c) => c.connectorId === input.connectorId);
  if (!spec) {
    throw new ActivationBlocked("UNKNOWN_CONNECTOR", `${input.connectorId} is not in inventory`);
  }
  if (spec.status === "prohibited") {
    throw new ActivationBlocked("GATE_032", "Outbound AI voice is prohibited.");
  }
  const now = input.now ?? new Date();
  const permitDigest = await redeemPermit(
    input.sql,
    input.tenantId,
    input.permit.permitId,
    input.permit.payloadDigest,
    now,
  );
  const grant = await grantStatus(input.sql, input.tenantId, input.connectorId);
  const attemptId = newId();
  const attemptState = grant && grant.status === "active" ? "confirmed" : "rejected";
  await insertCanonical(
    input.sql,
    {
      ...canonicalEnvelope({
        id: attemptId,
        tenantId: input.tenantId,
        recordType: "EffectAttempt",
        status: "current",
        createdBy: { actorType: "license_holder", actorId: input.licenseHolderId },
        sourceEvidenceIds: [input.permit.permitId],
        now,
      }),
      intentId: input.permit.intentId,
      actionClass: input.permit.actionClass,
      payloadDigest: input.permit.payloadDigest,
      permitDigest,
      idempotencyKey: input.permit.intentId,
      attemptState,
    },
    now,
  );
  if (!grant || grant.status !== "active") {
    throw new ActivationBlocked(
      "CONNECTOR_INACTIVE",
      `${input.connectorId} has no live grant. Permit was redeemed; no provider call was made.`,
    );
  }
  return {
    attemptId,
    attemptState: "confirmed" as const,
    providerReceiptId: newId(),
    payload: input.payload,
  };
}
