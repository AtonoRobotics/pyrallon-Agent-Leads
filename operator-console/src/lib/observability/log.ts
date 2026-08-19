import type { Sql } from "@/lib/db";
import { newId } from "@/lib/canonical/envelope.ts";

export async function logEvent(
  sql: Sql,
  input: {
    tenantId: string;
    kind: string;
    journeyId?: string;
    payload: Record<string, unknown>;
    now?: Date;
  },
) {
  await sql`
    insert into operational_events (tenant_id, id, kind, journey_id, payload, created_at)
    values (
      ${input.tenantId}, ${newId()}, ${input.kind}, ${input.journeyId ?? null},
      ${JSON.stringify(input.payload)}, ${(input.now ?? new Date()).toISOString()}
    )
  `;
}

export async function listEvents(sql: Sql, tenantId: string, journeyId?: string) {
  const rows = journeyId
    ? await sql<{ id: string; kind: string; payload: string; created_at: string }>`
        select id, kind, payload, created_at from operational_events
        where tenant_id = ${tenantId} and journey_id = ${journeyId}
        order by created_at asc
      `
    : await sql<{ id: string; kind: string; payload: string; created_at: string }>`
        select id, kind, payload, created_at from operational_events
        where tenant_id = ${tenantId}
        order by created_at desc
        limit 200
      `;
  return rows.map((r) => ({
    id: r.id,
    kind: r.kind,
    payload: JSON.parse(r.payload) as Record<string, unknown>,
    createdAt: r.created_at,
  }));
}
