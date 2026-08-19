import type { Sql } from "@/lib/db";
import { ContractViolation } from "@/lib/contracts/violations.ts";
import { iso, newId } from "@/lib/canonical/envelope.ts";
import { logEvent } from "@/lib/observability/log.ts";

export type WorkflowState = {
  ingressState: string;
  acknowledgmentState: string;
  qualificationState: string;
  consultationState: string;
  nurtureState: string;
  blockerCodes: string[];
};

const INITIAL: WorkflowState = {
  ingressState: "captured",
  acknowledgmentState: "not_required",
  qualificationState: "not_started",
  consultationState: "not_ready",
  nurtureState: "inactive",
  blockerCodes: [],
};

export function fold(events: Array<{ eventType: string; payload: Record<string, unknown> }>): WorkflowState {
  let state = { ...INITIAL, blockerCodes: [] as string[] };
  for (const event of events) {
    const p = event.payload;
    switch (event.eventType) {
      case "IngressAdmitted":
        state = { ...state, ingressState: String(p.ingressState ?? "identified") };
        break;
      case "IdentityAmbiguous":
        state = {
          ...state,
          ingressState: "identity_ambiguous",
          blockerCodes: unique([...state.blockerCodes, "identity_unresolved"]),
        };
        break;
      case "AckAttempted":
        state = { ...state, acknowledgmentState: String(p.acknowledgmentState ?? "pending") };
        break;
      case "QualificationUpdated":
        state = { ...state, qualificationState: String(p.qualificationState ?? state.qualificationState) };
        break;
      case "ConsultProposed":
        state = { ...state, consultationState: "offering" };
        break;
      case "TimerFired":
        state = {
          ...state,
          blockerCodes: unique([...state.blockerCodes, String(p.code ?? "timer")]),
        };
        break;
      case "Blocked":
        state = {
          ...state,
          consultationState: "blocked",
          blockerCodes: unique([...state.blockerCodes, String(p.code ?? "blocked")]),
        };
        break;
      default:
        break;
    }
  }
  return state;
}

export async function startJourneyWorkflow(
  sql: Sql,
  input: { tenantId: string; journeyId: string; now?: Date },
) {
  const now = iso(input.now);
  const workflowId = `buyer-journey:${input.journeyId}`;
  const existing = await sql<{ workflow_id: string }>`
    select workflow_id from workflow_instances
    where tenant_id = ${input.tenantId} and workflow_id = ${workflowId}
  `;
  if (existing[0]) return { workflowId, created: false };
  await sql`
    insert into workflow_instances (
      tenant_id, workflow_id, workflow_type, journey_id, run_id, status,
      canonical_version, state, created_at, updated_at
    ) values (
      ${input.tenantId}, ${workflowId}, ${"BuyerJourneyWorkflow"}, ${input.journeyId},
      ${newId()}, ${"running"}, 0, ${JSON.stringify(INITIAL)}, ${now}, ${now}
    )
  `;
  await appendHistory(sql, {
    tenantId: input.tenantId,
    workflowId,
    eventType: "IngressAdmitted",
    eventId: `ingress:${input.journeyId}`,
    payload: { ingressState: "identified", journeyId: input.journeyId },
    now: input.now,
  });
  return { workflowId, created: true };
}

export async function signalWorkflow(
  sql: Sql,
  input: {
    tenantId: string;
    workflowId: string;
    eventType: string;
    eventId: string;
    payload: Record<string, unknown>;
    now?: Date;
  },
) {
  const lease = await acquireLease(sql, input.tenantId, input.workflowId, input.now);
  try {
    const dup = await sql<{ event_id: string }>`
      select event_id from workflow_history
      where tenant_id = ${input.tenantId} and workflow_id = ${input.workflowId} and event_id = ${input.eventId}
    `;
    if (dup[0]) return { duplicate: true as const };
    await appendHistory(sql, input);
    const replayed = await replay(sql, input.tenantId, input.workflowId);
    await sql`
      update workflow_instances
      set state = ${JSON.stringify(replayed)}, updated_at = ${iso(input.now)}
      where tenant_id = ${input.tenantId} and workflow_id = ${input.workflowId}
    `;
    await logEvent(sql, {
      tenantId: input.tenantId,
      kind: "workflow_signal",
      payload: { workflowId: input.workflowId, eventType: input.eventType, eventId: input.eventId },
      now: input.now,
    });
    return { duplicate: false as const, state: replayed };
  } finally {
    await sql`
      delete from workflow_leases
      where tenant_id = ${input.tenantId} and workflow_id = ${input.workflowId} and owner = ${lease}
    `;
  }
}

export async function replay(sql: Sql, tenantId: string, workflowId: string): Promise<WorkflowState> {
  const rows = await sql<{ event_type: string; payload: string }>`
    select event_type, payload from workflow_history
    where tenant_id = ${tenantId} and workflow_id = ${workflowId}
    order by event_seq asc
  `;
  return fold(rows.map((r) => ({ eventType: r.event_type, payload: JSON.parse(r.payload) })));
}

export async function scheduleTimer(
  sql: Sql,
  input: { tenantId: string; workflowId: string; timerId: string; fireAt: Date },
) {
  await sql`
    insert into workflow_timers (tenant_id, workflow_id, timer_id, fire_at, status)
    values (
      ${input.tenantId}, ${input.workflowId}, ${input.timerId}, ${input.fireAt.toISOString()}, ${"pending"}
    )
    on conflict (tenant_id, workflow_id, timer_id) do update set fire_at = excluded.fire_at, status = ${"pending"}
  `;
}

export async function fireDueTimers(sql: Sql, tenantId: string, now = new Date()) {
  const due = await sql<{ workflow_id: string; timer_id: string }>`
    select workflow_id, timer_id from workflow_timers
    where tenant_id = ${tenantId} and status = ${"pending"} and fire_at <= ${iso(now)}
  `;
  const fired = [];
  for (const timer of due) {
    await sql`
      update workflow_timers set status = ${"fired"}
      where tenant_id = ${tenantId} and workflow_id = ${timer.workflow_id} and timer_id = ${timer.timer_id}
        and status = ${"pending"}
    `;
    await signalWorkflow(sql, {
      tenantId,
      workflowId: timer.workflow_id,
      eventType: "TimerFired",
      eventId: `timer:${timer.timer_id}`,
      payload: { timerId: timer.timer_id, code: "timer_fired" },
      now,
    });
    fired.push(timer.timer_id);
  }
  return fired;
}

async function appendHistory(
  sql: Sql,
  input: {
    tenantId: string;
    workflowId: string;
    eventType: string;
    eventId: string;
    payload: Record<string, unknown>;
    now?: Date;
  },
) {
  const last = await sql<{ event_seq: number }>`
    select event_seq from workflow_history
    where tenant_id = ${input.tenantId} and workflow_id = ${input.workflowId}
    order by event_seq desc limit 1
  `;
  const seq = Number(last[0]?.event_seq ?? 0) + 1;
  await sql`
    insert into workflow_history (
      tenant_id, workflow_id, event_seq, event_type, event_id, payload, created_at
    ) values (
      ${input.tenantId}, ${input.workflowId}, ${seq}, ${input.eventType}, ${input.eventId},
      ${JSON.stringify(input.payload)}, ${iso(input.now)}
    )
  `;
}

async function acquireLease(sql: Sql, tenantId: string, workflowId: string, now?: Date) {
  const owner = newId();
  const expires = new Date((now ?? new Date()).getTime() + 15_000).toISOString();
  await sql`delete from workflow_leases where tenant_id = ${tenantId} and expires_at < ${iso(now)}`;
  try {
    await sql`
      insert into workflow_leases (tenant_id, workflow_id, owner, expires_at)
      values (${tenantId}, ${workflowId}, ${owner}, ${expires})
    `;
  } catch {
    throw new ContractViolation([
      { code: "WORKFLOW_LEASE_HELD", path: "$.workflowId", message: "another worker holds the lease" },
    ]);
  }
  return owner;
}

function unique(values: string[]) {
  return [...new Set(values)];
}
