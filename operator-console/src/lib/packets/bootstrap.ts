import type { Sql } from "@/lib/db";
import { listRecords, type StoredRecord } from "@/lib/canonical/store.ts";
import type { JourneyRecord, PersonRecord } from "@/lib/canonical/predicates.ts";
import { ensureDefaultGrants } from "@/lib/connectors/gateway.ts";
import { startJourneyWorkflow } from "@/lib/workflow/runtime.ts";
import { rebuildEndpointIndex } from "@/lib/canonical/reconstruct.ts";
import { recordDefaultGates } from "@/lib/packets/gates.ts";

export async function bootstrapPackets(sql: Sql, tenantId: string) {
  await ensureDefaultGrants(sql, tenantId);
  await rebuildEndpointIndex(sql, tenantId);
  const journeys = await listRecords<JourneyRecord>(sql, tenantId, "BuyerJourney");
  for (const journey of journeys) {
    await startJourneyWorkflow(sql, { tenantId, journeyId: journey.id });
  }
  await recordDefaultGates(sql);
}

export async function peopleForTenant(sql: Sql, tenantId: string) {
  return listRecords<PersonRecord>(sql, tenantId, "Person");
}

export async function partiesForTenant(sql: Sql, tenantId: string) {
  return listRecords<StoredRecord & { members: Array<{ personId: string; role: string }> }>(
    sql,
    tenantId,
    "BuyingParty",
  );
}
