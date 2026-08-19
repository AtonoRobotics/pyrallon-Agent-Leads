import { Link, createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  denyOutboundEffect,
  getJourney,
  proposeAppointment,
  recordAssertion,
  recordSuppression,
  recomputeReadiness,
  resolveRepresentation,
  validateShowingAgreement,
} from "@/lib/ops/server";
import type { JourneyDetail } from "@/lib/ops/types";
import { CRITERION_LABELS, SOURCE_LABELS } from "@/lib/ops/types";
import { formatRelative, formatWhen } from "@/lib/ops/format";
import { PageHeader, Panel, SectionLabel, UnderlineTabs } from "@/components/ops/shell";
import { StateChip } from "@/components/ops/state-chip";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

export const Route = createFileRoute("/ops/journeys/$id")({ component: JourneyPage });

type Tab = "briefing" | "thread" | "contracts";

const TABS = [
  { id: "briefing", label: "Briefing" },
  { id: "thread", label: "Thread" },
  { id: "contracts", label: "Contracts" },
] as const;

function JourneyPage() {
  const { id } = Route.useParams();
  const [data, setData] = useState<JourneyDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("briefing");
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState({ criterion: "budget_financing", value: "" });
  const [repNote, setRepNote] = useState("");
  const [term, setTerm] = useState("2030-01-15T00:00:00Z");
  const [allowAdvice, setAllowAdvice] = useState(false);

  const reload = useCallback(() => {
    return getJourney({ data: id })
      .then(setData)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Failed to load"));
  }, [id]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    try {
      const result = await fn();
      if (
        result &&
        typeof result === "object" &&
        "ok" in result &&
        (result as { ok: boolean }).ok === false
      ) {
        const violations =
          (result as { violations?: Array<{ code: string }> }).violations ?? [];
        toast.error(violations.map((v) => v.code).join(", ") || "Denied");
        return result;
      }
      toast.success("Admitted");
      await reload();
      return result;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Denied");
      return null;
    } finally {
      setBusy(null);
    }
  }

  if (error) return <p className="text-sm text-danger">{error}</p>;
  if (!data) return <div className="h-64 animate-pulse rounded-lg bg-surface" />;

  const { journey, person } = data;
  const suppressed = journey.contactability === "suppressed";

  return (
    <div>
      <p className="mb-3 text-xs text-muted">
        <Link to="/ops" className="hover:text-fg">
          Pipeline
        </Link>
        <span className="mx-1.5 text-subtle">/</span>
        {person.displayName}
      </p>
      <PageHeader
        title={person.displayName}
        description={`${person.email ?? "No email"} · ${person.phone ?? "No phone"}`}
        meta={`${SOURCE_LABELS[journey.source] ?? journey.source} · ${journey.serviceZone ?? "unspecified zone"} · Identity ${person.identityState}`}
      />

      <div className="mb-4 flex flex-wrap gap-1.5">
        <StateChip value={journey.journeyState} />
        <StateChip value={journey.contactability} />
        <StateChip value={journey.acknowledgment} />
        <StateChip value={journey.qualificationState} />
        <StateChip value={journey.consultationState} />
        <StateChip value={journey.representationState} />
      </div>

      {journey.blockerCodes.length > 0 ? (
        <Panel className="mb-4">
          <SectionLabel>Current blockers</SectionLabel>
          <p className="mt-1.5 font-mono text-xs text-fg">{journey.blockerCodes.join(" · ")}</p>
        </Panel>
      ) : null}

      <div className="mb-5 flex flex-wrap gap-2 rounded-lg border border-border bg-surface p-2">
        <Button
          size="sm"
          disabled={!!busy}
          onClick={() => run("ready", () => recomputeReadiness({ data: id }))}
        >
          Recompute readiness
        </Button>
        <Button
          size="sm"
          variant="secondary"
          disabled={!!busy || journey.journeyState !== "consultation_ready"}
          onClick={() => {
            const start = new Date();
            start.setDate(start.getDate() + 2);
            start.setHours(16, 0, 0, 0);
            return run("slot", () =>
              proposeAppointment({ data: { journeyId: id, startsAt: start.toISOString() } }),
            );
          }}
        >
          Propose local slot
        </Button>
        <Button
          size="sm"
          variant="secondary"
          disabled={!!busy}
          onClick={() =>
            run("ack", () => denyOutboundEffect({ data: { kind: "ack", journeyId: id } }))
          }
        >
          Send acknowledgment
        </Button>
        <Button
          size="sm"
          variant="secondary"
          disabled={!!busy}
          onClick={() =>
            run("book", () => denyOutboundEffect({ data: { kind: "calendar", journeyId: id } }))
          }
        >
          Confirm on calendar
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={!!busy || suppressed}
          onClick={() => run("stop", () => recordSuppression({ data: id }))}
        >
          Record STOP
        </Button>
      </div>

      <div className="mb-4">
        <UnderlineTabs value={tab} options={TABS} onChange={setTab} />
      </div>

      {tab === "briefing" ? (
        <div className="grid gap-3 lg:grid-cols-[1.4fr_0.8fr]">
          <Panel>
            <SectionLabel>Briefing</SectionLabel>
            <p className="mt-1 text-sm text-muted">
              Assertions stay assertions. A model cannot write a verified fact.
            </p>
            <ul className="mt-4 divide-y divide-border">
              {data.observations.length === 0 ? (
                <li className="py-2 text-sm text-muted">No qualification items.</li>
              ) : (
                data.observations.map((o) => (
                  <li key={o.id} className="py-3 first:pt-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <p className="text-sm font-medium">
                        {CRITERION_LABELS[o.criterion] ?? o.criterion}
                      </p>
                      <StateChip value={o.epistemicType} />
                      <StateChip value={o.observationState} />
                    </div>
                    <p className="mt-1 text-sm leading-relaxed">{o.value}</p>
                  </li>
                ))
              )}
            </ul>
            <form
              className="mt-4 space-y-3 border-t border-border pt-4"
              onSubmit={(e) => {
                e.preventDefault();
                if (!note.value.trim()) return;
                void run("note", () =>
                  recordAssertion({
                    data: { journeyId: id, criterion: note.criterion, value: note.value },
                  }),
                ).then(() => setNote((n) => ({ ...n, value: "" })));
              }}
            >
              <Label htmlFor="crit">Admit an assertion</Label>
              <Select
                id="crit"
                value={note.criterion}
                onChange={(e) => setNote((n) => ({ ...n, criterion: e.target.value }))}
              >
                {Object.entries(CRITERION_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </Select>
              <Textarea
                value={note.value}
                onChange={(e) => setNote((n) => ({ ...n, value: e.target.value }))}
                placeholder="Buyer-stated fact, stored as an assertion"
              />
              <Button type="submit" size="sm" disabled={!!busy}>
                Admit assertion
              </Button>
            </form>
          </Panel>
          <div className="space-y-3">
            <Panel>
              <SectionLabel>Appointments</SectionLabel>
              {data.appointments.length === 0 ? (
                <p className="mt-2 text-sm text-muted">None. Proposed is the maximum state here.</p>
              ) : (
                data.appointments.map((a) => (
                  <div key={a.id} className="mt-2">
                    <StateChip value={a.state} />
                    <p className="mt-1.5 text-sm">{formatWhen(a.startsAt)}</p>
                    <p className="text-xs text-subtle">{a.locationOrMode}</p>
                  </div>
                ))
              )}
            </Panel>
            {data.cases.filter((c) => c.status === "open").map((ex) => (
              <Panel key={ex.id}>
                <SectionLabel>{ex.kind}</SectionLabel>
                <p className="mt-1.5 text-sm">{ex.detail}</p>
                <Link
                  to="/ops/exceptions"
                  className="mt-2 inline-block text-xs font-medium text-accent hover:underline"
                >
                  Open identity cases
                </Link>
              </Panel>
            ))}
            {journey.representationState === "conflict" ? (
              <Panel>
                <SectionLabel>Representation conflict</SectionLabel>
                <Textarea
                  className="mt-2"
                  value={repNote}
                  onChange={(e) => setRepNote(e.target.value)}
                  placeholder="Inspection note after checking agreements on file"
                />
                <Button
                  className="mt-3"
                  size="sm"
                  disabled={!!busy}
                  onClick={() =>
                    run("rep", () =>
                      resolveRepresentation({ data: { journeyId: id, note: repNote } }),
                    )
                  }
                >
                  Record inspection
                </Button>
              </Panel>
            ) : null}
          </div>
        </div>
      ) : null}

      {tab === "thread" ? (
        <div className="grid gap-3 md:grid-cols-2">
          <Panel>
            <SectionLabel>Inbound record</SectionLabel>
            <ul className="mt-3 space-y-4">
              {data.messages.map((m) => (
                <li key={m.id}>
                  <p className="text-xs text-muted">
                    {m.direction} · {m.channel} · {m.deliveryState} · {formatRelative(m.createdAt)}
                  </p>
                  <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed">{m.body}</p>
                </li>
              ))}
            </ul>
          </Panel>
          <Panel>
            <SectionLabel>Commitments</SectionLabel>
            {data.commitments.length === 0 ? (
              <p className="mt-2 text-sm text-muted">No open commitments.</p>
            ) : (
              data.commitments.map((c) => (
                <div key={c.id} className="mt-2">
                  <StateChip value={c.state} />
                  <p className="mt-1.5 text-sm">{c.description}</p>
                </div>
              ))
            )}
            <div className="mt-4 border-t border-border pt-3">
              <SectionLabel>Consent</SectionLabel>
              <ul className="mt-2 space-y-2">
                {data.consent.map((c) => (
                  <li key={c.id} className="flex flex-wrap items-center gap-2 text-sm">
                    <StateChip value={c.status} />
                    <span>
                      {c.channel} · {c.purpose}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </Panel>
        </div>
      ) : null}

      {tab === "contracts" ? (
        <Panel className="max-w-xl">
          <SectionLabel>Showing-only agreement admission</SectionLabel>
          <p className="mt-1.5 text-sm text-muted">
            This runs the governing semantic validator. A 15-day term or advice service must fail.
          </p>
          <label className="mt-4 block space-y-1.5">
            <Label>terminatesAt</Label>
            <input
              className="field font-mono"
              value={term}
              onChange={(e) => setTerm(e.target.value)}
            />
          </label>
          <label className="mt-3 flex min-h-10 items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="size-4 accent-accent"
              checked={allowAdvice}
              onChange={(e) => setAllowAdvice(e.target.checked)}
            />
            Allow property advice
          </label>
          <Button
            className="mt-4"
            size="sm"
            disabled={!!busy}
            onClick={() =>
              run("agree", async () => {
                const res = await validateShowingAgreement({
                  data: { terminatesAt: term, allowAdvice },
                });
                toast.message(res.ok ? "Admitted" : res.violations.map((v) => v.code).join(", "));
                return res;
              })
            }
          >
            Validate draft
          </Button>
        </Panel>
      ) : null}
    </div>
  );
}
