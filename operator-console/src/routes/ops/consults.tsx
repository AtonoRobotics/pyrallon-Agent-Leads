import { Link, createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { getDashboard } from "@/lib/ops/server";
import type { DashboardPayload } from "@/lib/ops/types";
import { formatWhen } from "@/lib/ops/format";
import { EmptyState, PageHeader, Panel, SectionLabel } from "@/components/ops/shell";
import { StateChip } from "@/components/ops/state-chip";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/ops/consults")({ component: ConsultsPage });

function ConsultsPage() {
  const [data, setData] = useState<DashboardPayload | null>(null);

  useEffect(() => {
    void getDashboard().then(setData);
  }, []);

  if (!data) return <div className="h-48 animate-pulse rounded-lg bg-surface" />;

  const proposed = data.appointments.filter((a) => a.state === "proposed");
  const ready = data.journeys.filter((j) => j.journeyState === "consultation_ready");

  return (
    <div>
      <PageHeader
        title="Consults"
        description="An appointment here is proposed. Confirmed requires a provider receipt and a Habitat permit."
      />

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel>
          <SectionLabel>Proposed</SectionLabel>
          {proposed.length === 0 ? (
            <EmptyState
              title="No proposed consults"
              body="Readiness can create a local proposed slot. It cannot confirm one."
            />
          ) : (
            <ul className="mt-3 divide-y divide-border">
              {proposed.map((a) => {
                const j = data.journeys.find((row) => row.id === a.journeyId);
                return (
                  <li key={a.id} className="py-3 first:pt-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-medium">{j?.person.displayName ?? "Buyer"}</p>
                      <StateChip value={a.state} />
                    </div>
                    <p className="mt-1 text-sm text-muted">{formatWhen(a.startsAt)}</p>
                    {j ? (
                      <Link
                        to="/ops/journeys/$id"
                        params={{ id: j.id }}
                        className="mt-1 inline-block text-xs font-medium text-accent hover:underline"
                      >
                        Open record
                      </Link>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </Panel>
        <Panel>
          <SectionLabel>Consultation ready</SectionLabel>
          {ready.length === 0 ? (
            <EmptyState
              title="No journey is ready"
              body="The predicate is computed from identity, suppression, representation, and required observations."
            />
          ) : (
            <ul className="mt-3 divide-y divide-border">
              {ready.map((j) => (
                <li key={j.id} className="flex items-center justify-between gap-3 py-3 first:pt-2">
                  <div>
                    <p className="text-sm font-medium">{j.person.displayName}</p>
                    <p className="text-xs text-muted">{j.serviceZone}</p>
                  </div>
                  <Button asChild size="sm" variant="secondary">
                    <Link to="/ops/journeys/$id" params={{ id: j.id }}>
                      Propose slot
                    </Link>
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}
