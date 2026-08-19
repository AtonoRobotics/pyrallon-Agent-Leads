import type { Sql } from "@/lib/db";
import { admitOntologyRecord } from "@/lib/contracts/validate.ts";
import { ContractViolation } from "@/lib/contracts/violations.ts";
import type { JsonRecord } from "@/lib/contracts/semantic.ts";
import { assertNotModelFact } from "./predicates.ts";
import { appendEvidence } from "@/lib/evidence/ledger.ts";

export type StoredRecord = JsonRecord & {
  id: string;
  tenantId: string;
  recordType: string;
  version: number;
  status: string;
};

export async function insertCanonical(sql: Sql, record: JsonRecord, now = new Date()) {
  admitOntologyRecord(record, now);
  if (record.recordType === "EpistemicItem") {
    assertNotModelFact({
      epistemicType: String(record.epistemicType),
      speakerOrMethodRef: record.speakerOrMethodRef ? String(record.speakerOrMethodRef) : undefined,
    });
  }
  const tenantId = String(record.tenantId);
  const id = String(record.id);
  await sql`
    insert into canonical_records (
      tenant_id, id, record_type, schema_version, version, status,
      effective_from, effective_to, payload, created_at, updated_at
    ) values (
      ${tenantId}, ${id}, ${String(record.recordType)}, ${String(record.schemaVersion)},
      ${Number(record.version)}, ${String(record.status)},
      ${String(record.effectiveFrom)}, ${record.effectiveTo ? String(record.effectiveTo) : null},
      ${JSON.stringify(record)}, ${String(record.createdAt)}, ${String(record.updatedAt)}
    )
  `;
  await appendEvidence(sql, {
    tenantId,
    recordId: id,
    journeyId:
      record.recordType === "BuyerJourney"
        ? id
        : record.journeyId
          ? String(record.journeyId)
          : undefined,
    payload: {
      kind: "canonical_insert",
      recordType: record.recordType,
      id,
      version: record.version,
    },
    now,
  });
  return record as StoredRecord;
}

export async function updateCanonical(
  sql: Sql,
  tenantId: string,
  current: StoredRecord,
  patch: JsonRecord,
  now = new Date(),
) {
  const next: JsonRecord = {
    ...current,
    ...patch,
    version: Number(current.version) + 1,
    updatedAt: now.toISOString(),
  };
  admitOntologyRecord(next, now);
  if (next.recordType === "EpistemicItem") {
    assertNotModelFact({
      epistemicType: String(next.epistemicType),
      speakerOrMethodRef: next.speakerOrMethodRef ? String(next.speakerOrMethodRef) : undefined,
    });
  }
  const rows = await sql<{ version: number }>`
    update canonical_records
    set version = ${next.version},
        status = ${String(next.status)},
        payload = ${JSON.stringify(next)},
        updated_at = ${next.updatedAt},
        effective_to = ${next.effectiveTo ? String(next.effectiveTo) : null}
    where tenant_id = ${tenantId} and id = ${current.id} and version = ${current.version}
    returning version
  `;
  if (!rows[0]) {
    throw new ContractViolation([
      {
        code: "VERSION_CONFLICT",
        path: "$.version",
        message: "optimistic version mismatch",
      },
    ]);
  }
  await appendEvidence(sql, {
    tenantId,
    recordId: String(current.id),
    payload: {
      kind: "canonical_update",
      recordType: next.recordType,
      id: current.id,
      version: next.version,
      supersedes: current.version,
    },
    now,
  });
  return next as StoredRecord;
}

export async function getRecord<T extends StoredRecord>(
  sql: Sql,
  tenantId: string,
  id: string,
): Promise<T | null> {
  const rows = await sql<{ payload: string }>`
    select payload from canonical_records where tenant_id = ${tenantId} and id = ${id}
  `;
  if (!rows[0]) return null;
  return JSON.parse(rows[0].payload) as T;
}

export async function listRecords<T extends StoredRecord>(
  sql: Sql,
  tenantId: string,
  recordType: string,
): Promise<T[]> {
  const rows = await sql<{ payload: string }>`
    select payload from canonical_records
    where tenant_id = ${tenantId} and record_type = ${recordType}
    order by updated_at desc
  `;
  return rows.map((row) => JSON.parse(row.payload) as T);
}

export async function listByField<T extends StoredRecord>(
  sql: Sql,
  tenantId: string,
  recordType: string,
  field: string,
  value: string,
): Promise<T[]> {
  const all = await listRecords<T>(sql, tenantId, recordType);
  return all.filter((row) => String((row as JsonRecord)[field]) === value);
}
