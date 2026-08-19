import type { Sql } from "@/lib/db";
import { iso, newId } from "@/lib/canonical/envelope.ts";
import { ContractViolation } from "@/lib/contracts/violations.ts";

export type LocalSlot = {
  startsAt: string;
  endsAt: string;
  timeZone: "America/Chicago";
  label: string;
};

const ZONE = "America/Chicago";
const DURATION_MS = 45 * 60_000;

function chicagoParts(date: Date) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: ZONE,
    weekday: "short",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return {
    weekday: get("weekday"),
    year: Number(get("year")),
    month: Number(get("month")),
    day: Number(get("day")),
    hour: Number(get("hour")),
    minute: Number(get("minute")),
  };
}

function atChicago(year: number, month: number, day: number, hour: number, minute: number) {
  const utcGuess = Date.UTC(year, month - 1, day, hour + 6, minute, 0);
  const asDate = new Date(utcGuess);
  const parts = chicagoParts(asDate);
  const driftHours = parts.hour + parts.minute / 60 - (hour + minute / 60);
  const adjusted = new Date(utcGuess - driftHours * 3600_000);
  return adjusted;
}

export function localPolicySlots(now = new Date(), count = 6): LocalSlot[] {
  const slots: LocalSlot[] = [];
  const start = new Date(now.getTime());
  for (let day = 1; day <= 14 && slots.length < count; day += 1) {
    const cursor = new Date(start.getTime() + day * 24 * 3600_000);
    const parts = chicagoParts(cursor);
    if (parts.weekday === "Sat" || parts.weekday === "Sun") continue;
    for (const hour of [10, 14]) {
      if (slots.length >= count) break;
      const begins = atChicago(parts.year, parts.month, parts.day, hour, 0);
      if (begins.getTime() <= now.getTime()) continue;
      const ends = new Date(begins.getTime() + DURATION_MS);
      slots.push({
        startsAt: begins.toISOString(),
        endsAt: ends.toISOString(),
        timeZone: ZONE,
        label: hour === 10 ? "Morning consult" : "Afternoon consult",
      });
    }
  }
  return slots;
}

export async function persistSlotSet(
  sql: Sql,
  input: { tenantId: string; journeyId: string; slots: LocalSlot[]; now?: Date },
) {
  if (!input.slots.length) {
    throw new ContractViolation([
      { code: "NO_POLICY_SLOTS", path: "$.slots", message: "policy produced no future slots" },
    ]);
  }
  const now = input.now ?? new Date();
  const slotSetId = newId();
  const expiresAt = new Date(now.getTime() + 48 * 3600_000).toISOString();
  await sql`
    insert into slot_sets (tenant_id, slot_set_id, journey_id, version, slots, expires_at, created_at)
    values (
      ${input.tenantId}, ${slotSetId}, ${input.journeyId}, ${"policy/0.1.0"},
      ${JSON.stringify(input.slots)}, ${expiresAt}, ${iso(now)}
    )
  `;
  return { slotSetId, expiresAt, slots: input.slots };
}

export async function getSlotSet(sql: Sql, tenantId: string, slotSetId: string) {
  const rows = await sql<{ slots: string; expires_at: string; journey_id: string }>`
    select slots, expires_at, journey_id from slot_sets
    where tenant_id = ${tenantId} and slot_set_id = ${slotSetId}
  `;
  if (!rows[0]) return null;
  return {
    journeyId: rows[0].journey_id,
    expiresAt: rows[0].expires_at,
    slots: JSON.parse(rows[0].slots) as LocalSlot[],
  };
}
