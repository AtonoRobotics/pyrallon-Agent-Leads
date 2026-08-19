import type { Sql } from "@/lib/db";
import { listRecords, type StoredRecord } from "./store.ts";
import type { JourneyRecord, PersonRecord } from "./predicates.ts";
import { iso } from "./envelope.ts";
import { verifyChain } from "@/lib/evidence/ledger.ts";
import { replay } from "@/lib/workflow/runtime.ts";

export async function reconstructTenant(sql: Sql, tenantId: string) {
  const people = await listRecords<PersonRecord>(sql, tenantId, "Person");
  const parties = await listRecords<StoredRecord & { members: Array<{ personId: string; role: string }> }>(
    sql,
    tenantId,
    "BuyingParty",
  );
  const journeys = await listRecords<JourneyRecord>(sql, tenantId, "BuyerJourney");
  const consents = await listRecords(sql, tenantId, "ConsentGrant");
  const suppressions = await listRecords(sql, tenantId, "Suppression");
  const observations = await listRecords(sql, tenantId, "QualificationObservation");
  const appointments = await listRecords(sql, tenantId, "Appointment");
  const messages = await sql<{ journey_id: string; id: string }>`
    select journey_id, id from operational_messages where tenant_id = ${tenantId}
  `;
  const chain = await verifyChain(sql, tenantId);
  const workflows = [];
  for (const journey of journeys) {
    const workflowId = `buyer-journey:${journey.id}`;
    const exists = await sql<{ workflow_id: string }>`
      select workflow_id from workflow_instances
      where tenant_id = ${tenantId} and workflow_id = ${workflowId}
    `;
    if (exists[0]) {
      workflows.push({ workflowId, state: await replay(sql, tenantId, workflowId) });
    }
  }
  return {
    people,
    parties,
    journeys,
    consents,
    suppressions,
    observations,
    appointments,
    participantCount: people.length,
    journeyCount: journeys.length,
    messageCount: messages.length,
    evidenceEntries: chain.entries,
    evidenceTip: chain.tip,
    workflows,
    collapsed: false,
  };
}

export async function assertTenantIsolation(sql: Sql, tenantA: string, tenantB: string) {
  const a = await listRecords(sql, tenantA, "Person");
  const b = await listRecords(sql, tenantB, "Person");
  const aIds = new Set(a.map((p) => p.id));
  const leakedPeople = b.filter((p) => aIds.has(p.id));
  const aEvidence = await sql<{ id: string }>`
    select id from evidence_ledger where tenant_id = ${tenantA}
  `;
  const bEvidence = await sql<{ id: string }>`
    select id from evidence_ledger where tenant_id = ${tenantB}
  `;
  const aEv = new Set(aEvidence.map((r) => r.id));
  const leakedEvidence = bEvidence.filter((r) => aEv.has(r.id));
  return {
    leak: leakedPeople.length > 0 || leakedEvidence.length > 0,
    countA: a.length,
    countB: b.length,
  };
}

export async function rebuildEndpointIndex(sql: Sql, tenantId: string) {
  const people = await listRecords<PersonRecord>(sql, tenantId, "Person");
  const now = iso();
  for (const person of people) {
    for (const endpoint of person.endpoints ?? []) {
      await sql`
        insert into endpoint_index (tenant_id, endpoint_type, normalized_value, person_id, created_at)
        values (
          ${tenantId}, ${endpoint.type}, ${endpoint.normalizedValue}, ${person.id}, ${now}
        )
        on conflict (tenant_id, endpoint_type, normalized_value) do nothing
      `;
    }
  }
}
