import { createHash } from "node:crypto";
import type { Sql } from "@/lib/db";
import { newId, sha256Digest } from "@/lib/canonical/envelope.ts";
import { ContractViolation } from "@/lib/contracts/violations.ts";

const GENESIS = `sha256:${"0".repeat(64)}`;

export type EvidenceEntry = {
  tenantId: string;
  seq: number;
  id: string;
  prevHash: string;
  entryHash: string;
  artifactDigest: string;
  payload: Record<string, unknown>;
  retentionClass: string;
  recordId?: string;
  journeyId?: string;
  createdAt: string;
  tombstonedAt?: string | null;
};

function hashEntry(prevHash: string, artifactDigest: string, payload: string) {
  return sha256Digest(`${prevHash}|${artifactDigest}|${payload}`);
}

export async function appendEvidence(
  sql: Sql,
  input: {
    tenantId: string;
    payload: Record<string, unknown>;
    retentionClass?: string;
    recordId?: string;
    journeyId?: string;
    now?: Date;
  },
): Promise<EvidenceEntry> {
  const now = (input.now ?? new Date()).toISOString();
  const body = JSON.stringify(input.payload);
  const artifactDigest = sha256Digest(body);
  const last = await sql<{ seq: number; entry_hash: string }>`
    select seq, entry_hash from evidence_ledger
    where tenant_id = ${input.tenantId}
    order by seq desc limit 1
  `;
  const prevHash = last[0]?.entry_hash ?? GENESIS;
  const seq = Number(last[0]?.seq ?? 0) + 1;
  const id = newId();
  const entryHash = hashEntry(prevHash, artifactDigest, body);
  await sql`
    insert into evidence_ledger (
      tenant_id, seq, id, prev_hash, entry_hash, artifact_digest, payload,
      retention_class, record_id, journey_id, created_at
    ) values (
      ${input.tenantId}, ${seq}, ${id}, ${prevHash}, ${entryHash}, ${artifactDigest},
      ${body}, ${input.retentionClass ?? "operational"}, ${input.recordId ?? null},
      ${input.journeyId ?? null}, ${now}
    )
  `;
  if (seq % 10 === 0) {
    await sql`
      insert into evidence_checkpoints (tenant_id, seq, chain_hash, created_at)
      values (${input.tenantId}, ${seq}, ${entryHash}, ${now})
    `;
  }
  return {
    tenantId: input.tenantId,
    seq,
    id,
    prevHash,
    entryHash,
    artifactDigest,
    payload: input.payload,
    retentionClass: input.retentionClass ?? "operational",
    recordId: input.recordId,
    journeyId: input.journeyId,
    createdAt: now,
  };
}

export async function verifyChain(sql: Sql, tenantId: string) {
  const rows = await sql<{
    seq: number;
    prev_hash: string;
    entry_hash: string;
    artifact_digest: string;
    payload: string;
  }>`
    select seq, prev_hash, entry_hash, artifact_digest, payload
    from evidence_ledger where tenant_id = ${tenantId} order by seq asc
  `;
  let prev = GENESIS;
  for (const row of rows) {
    if (row.prev_hash !== prev) {
      throw new ContractViolation([
        {
          code: "EVIDENCE_CHAIN_BREAK",
          path: `$.seq.${row.seq}`,
          message: "prev_hash does not match prior entry",
        },
      ]);
    }
    const expected = hashEntry(row.prev_hash, row.artifact_digest, row.payload);
    if (expected !== row.entry_hash) {
      throw new ContractViolation([
        {
          code: "EVIDENCE_TAMPER",
          path: `$.seq.${row.seq}`,
          message: "entry hash does not match payload",
        },
      ]);
    }
    const digest = sha256Digest(row.payload);
    if (digest !== row.artifact_digest) {
      throw new ContractViolation([
        {
          code: "ARTIFACT_DIGEST_MISMATCH",
          path: `$.seq.${row.seq}`,
          message: "artifact digest does not match payload bytes",
        },
      ]);
    }
    prev = row.entry_hash;
  }
  return { entries: rows.length, tip: prev };
}

export async function tombstoneEvidence(sql: Sql, tenantId: string, id: string, now = new Date()) {
  const rows = await sql<{ id: string }>`
    update evidence_ledger
    set tombstoned_at = ${now.toISOString()}
    where tenant_id = ${tenantId} and id = ${id}
    returning id
  `;
  if (!rows[0]) {
    throw new ContractViolation([
      { code: "EVIDENCE_NOT_FOUND", path: "$.id", message: "cannot tombstone missing entry" },
    ]);
  }
}

export async function listLiveEvidence(sql: Sql, tenantId: string, journeyId?: string) {
  const rows = journeyId
    ? await sql<{ payload: string; tombstoned_at: string | null; seq: number; id: string }>`
        select payload, tombstoned_at, seq, id from evidence_ledger
        where tenant_id = ${tenantId} and journey_id = ${journeyId}
        order by seq asc
      `
    : await sql<{ payload: string; tombstoned_at: string | null; seq: number; id: string }>`
        select payload, tombstoned_at, seq, id from evidence_ledger
        where tenant_id = ${tenantId}
        order by seq asc
      `;
  return rows.filter((r) => !r.tombstoned_at).map((r) => ({
    id: r.id,
    seq: Number(r.seq),
    payload: JSON.parse(r.payload) as Record<string, unknown>,
  }));
}

export function sha256Hex(value: string) {
  return createHash("sha256").update(value).digest("hex");
}
