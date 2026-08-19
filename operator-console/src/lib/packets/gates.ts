import type { Sql } from "@/lib/db";
import { iso } from "@/lib/canonical/envelope.ts";

export type GateStatus = "pass" | "fail" | "blocked" | "prohibited";

export type GateRow = {
  gateId: string;
  status: GateStatus;
  evidenceRefs: string[];
  notes: string;
  recordedAt: string;
};

const DEFAULT_GATES: Array<Omit<GateRow, "recordedAt">> = [
  {
    gateId: "PKT-00-HASH",
    status: "pass",
    evidenceRefs: ["src/lib/contracts/registry.ts"],
    notes: "Ontology and gateway SHA-256 pins match contracts.manifest.json.",
  },
  {
    gateId: "GATE-032",
    status: "pass",
    evidenceRefs: ["src/lib/habitat/service.ts", "src/lib/connectors/gateway.ts"],
    notes: "outbound_ai_voice is rejected before permit issue. Voice connector is prohibited.",
  },
  {
    gateId: "HABITAT-IN-PROCESS",
    status: "pass",
    evidenceRefs: ["src/lib/habitat/service.ts"],
    notes: "In-process Habitat issues and redeems single-use permits. Not a hosted Habitat cluster.",
  },
  {
    gateId: "EVIDENCE-LEDGER",
    status: "pass",
    evidenceRefs: ["src/lib/evidence/ledger.ts", "migrations/0005_packets.sql"],
    notes: "Append-only chain with prev_hash, entry_hash, artifact_digest.",
  },
  {
    gateId: "FORM-INGRESS",
    status: "pass",
    evidenceRefs: ["src/lib/ingress/admit.ts"],
    notes: "ot01.inbound/1 form admission with inbound_events idempotency.",
  },
  {
    gateId: "TEMPORAL-CLOUD",
    status: "blocked",
    evidenceRefs: ["src/lib/workflow/runtime.ts"],
    notes: "In-process event-sourced workflow is running. Temporal Cloud is not configured and is not claimed.",
  },
  {
    gateId: "LIVE-EMAIL",
    status: "blocked",
    evidenceRefs: ["src/lib/connectors/gateway.ts"],
    notes: "email.outbound has no live grant. Permit redeem then fail-closed. No provider send.",
  },
  {
    gateId: "LIVE-SMS",
    status: "blocked",
    evidenceRefs: ["src/lib/connectors/gateway.ts"],
    notes: "sms.outbound has no live grant.",
  },
  {
    gateId: "LIVE-CALENDAR",
    status: "blocked",
    evidenceRefs: ["src/lib/connectors/gateway.ts", "src/lib/slots/policy.ts"],
    notes: "Local proposed slots only. calendar.write has no OAuth grant.",
  },
  {
    gateId: "LIVE-COGNITION",
    status: "blocked",
    evidenceRefs: ["src/lib/cognition/compiler.ts"],
    notes: "Deterministic qualification is on. Live model route requires XAI_API_KEY and GATE-013 evidence, which are not activated.",
  },
  {
    gateId: "VOICE",
    status: "prohibited",
    evidenceRefs: ["GATE-032"],
    notes: "Outbound AI-generated voice is prohibited.",
  },
];

export async function recordDefaultGates(sql: Sql, now = new Date()) {
  for (const gate of DEFAULT_GATES) {
    await sql`
      insert into gate_evidence (gate_id, status, evidence_refs, recorded_at, notes)
      values (
        ${gate.gateId}, ${gate.status}, ${JSON.stringify(gate.evidenceRefs)},
        ${iso(now)}, ${gate.notes}
      )
      on conflict (gate_id) do update set
        status = excluded.status,
        evidence_refs = excluded.evidence_refs,
        recorded_at = excluded.recorded_at,
        notes = excluded.notes
    `;
  }
}

export async function listGates(sql: Sql): Promise<GateRow[]> {
  const rows = await sql<{
    gate_id: string;
    status: string;
    evidence_refs: string;
    recorded_at: string;
    notes: string;
  }>`
    select gate_id, status, evidence_refs, recorded_at, notes
    from gate_evidence
    order by gate_id
  `;
  return rows.map((r) => ({
    gateId: r.gate_id,
    status: r.status as GateStatus,
    evidenceRefs: JSON.parse(r.evidence_refs) as string[],
    notes: r.notes,
    recordedAt: r.recorded_at,
  }));
}

export function defaultGates() {
  return DEFAULT_GATES;
}
