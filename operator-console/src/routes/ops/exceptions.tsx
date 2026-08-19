import { Link, createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { getDashboard, resolveIdentity, resolveRepresentation } from "@/lib/ops/server";
import type { DashboardPayload } from "@/lib/ops/types";
import { formatRelative } from "@/lib/ops/format";
import { EmptyState, PageHeader, Panel, SectionLabel } from "@/components/ops/shell";
import { StateChip } from "@/components/ops/state-chip";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export const Route = createFileRoute("/ops/exceptions")({ component: ExceptionsPage });

function ExceptionsPage() {
  const [data, setData] = useState<DashboardPayload | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    void getDashboard().then(setData);
  }, []);

  if (!data) return <div className="h-48 animate-pulse rounded-lg bg-surface" />;

  const identity = data.cases.filter((e) => e.status === "open");
  const conflicts = data.journeys.filter((j) => j.representationState === "conflict");

  async function resolveCase(id: string) {
    setBusy(id);
    try {
      await resolveIdentity({
        data: { caseId: id, note: notes[id] || "" },
      });
      toast.success("Identity admitted as resolved");
      setData(await getDashboard());
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Denied");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <PageHeader
        title="Exceptions"
        description="Identity merges require a confirmed endpoint. Representation conflicts require an agreement inspection."
      />

      {identity.length === 0 && conflicts.length === 0 ? (
        <Panel>
          <EmptyState
            title="No open cases"
            body="Ambiguous identity and representation conflicts land here."
          />
        </Panel>
      ) : null}

      <div className="space-y-3">
        {identity.map((ex) => {
          const journey = data.journeys.find((j) => j.id === ex.journeyId);
          return (
            <Panel key={ex.id}>
              <div className="flex flex-wrap items-center gap-2">
                <StateChip value={ex.kind} />
                <StateChip value={ex.status} />
              </div>
              <h2 className="mt-2 text-base font-semibold tracking-tight">{ex.title}</h2>
              <p className="mt-1.5 text-sm leading-relaxed text-muted">{ex.detail}</p>
              {journey ? (
                <p className="mt-2 text-sm">
                  <Link
                    to="/ops/journeys/$id"
                    params={{ id: journey.id }}
                    className="font-medium underline-offset-4 hover:underline"
                  >
                    {journey.person.displayName}
                  </Link>
                  <span className="text-muted"> · {formatRelative(ex.createdAt)}</span>
                </p>
              ) : null}
              <Textarea
                className="mt-3"
                value={notes[ex.id] ?? ""}
                onChange={(e) => setNotes((n) => ({ ...n, [ex.id]: e.target.value }))}
                placeholder="Which endpoint did you confirm, and how?"
              />
              <Button className="mt-3" size="sm" disabled={busy === ex.id} onClick={() => void resolveCase(ex.id)}>
                Resolve identity
              </Button>
            </Panel>
          );
        })}

        {conflicts.map((j) => (
          <Panel key={j.id}>
            <StateChip value="conflict" />
            <h2 className="mt-2 text-base font-semibold tracking-tight">{j.person.displayName}</h2>
            <p className="mt-1.5 text-sm text-muted">
              Two representation statements are on file. Consultation readiness stays blocked until
              you inspect agreements.
            </p>
            <SectionLabel>
              <span className="mt-3 block">Inspection note</span>
            </SectionLabel>
            <Textarea
              className="mt-1.5"
              value={notes[j.id] ?? ""}
              onChange={(e) => setNotes((n) => ({ ...n, [j.id]: e.target.value }))}
              placeholder="Inspection note"
            />
            <Button
              className="mt-3"
              size="sm"
              disabled={busy === j.id}
              onClick={() => {
                setBusy(j.id);
                resolveRepresentation({ data: { journeyId: j.id, note: notes[j.id] || "" } })
                  .then(async () => {
                    toast.success("Inspection recorded");
                    setData(await getDashboard());
                  })
                  .catch((err: unknown) => toast.error(err instanceof Error ? err.message : "Denied"))
                  .finally(() => setBusy(null));
              }}
            >
              Record inspection
            </Button>
          </Panel>
        ))}
      </div>
    </div>
  );
}
