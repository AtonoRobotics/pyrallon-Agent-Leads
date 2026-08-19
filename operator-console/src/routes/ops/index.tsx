import { Link, createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { getDashboard } from "@/lib/ops/server";
import type { DashboardPayload, JourneyCard } from "@/lib/ops/types";
import { SOURCE_LABELS } from "@/lib/ops/types";
import { formatRelative, prettyState } from "@/lib/ops/format";
import { EmptyState, PageHeader, Panel, Segmented } from "@/components/ops/shell";
import { StateChip } from "@/components/ops/state-chip";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/ops/")({ component: Pipeline });

const FILTERS = [
  { id: "all", label: "All" },
  { id: "needs_you", label: "Needs you" },
  { id: "ready", label: "Consult ready" },
  { id: "blocked", label: "Blocked" },
] as const;

function Pipeline() {
  const [data, setData] = useState<DashboardPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["id"]>("all");

  useEffect(() => {
    getDashboard()
      .then(setData)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Failed to load"));
  }, []);

  const rows = useMemo(() => {
    const list = data?.journeys ?? [];
    if (filter === "needs_you") {
      return list.filter((j) => j.openCases > 0 || j.representationState === "conflict");
    }
    if (filter === "ready") return list.filter((j) => j.journeyState === "consultation_ready");
    if (filter === "blocked") {
      return list.filter((j) => j.journeyState === "blocked" || j.blockerCodes.length > 0);
    }
    return list;
  }, [data, filter]);

  if (error) return <p className="text-sm text-danger">{error}</p>;
  if (!data) {
    return (
      <div className="space-y-4">
        <div className="h-8 w-48 animate-pulse rounded-md bg-raised" />
        <div className="h-40 animate-pulse rounded-lg bg-surface" />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Pipeline"
        description="Active buyer journeys. No provider send or calendar write has been admitted."
        meta={`${data.tenant.brokerageName} · ${data.tenant.agentName} · ${data.tenant.licenseNumber}`}
        action={
          <Button asChild>
            <Link to="/ops/capture">Capture form</Link>
          </Button>
        }
      />

      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Active" value={data.stats.active} />
        <Stat label="Consult ready" value={data.stats.ready} />
        <Stat label="Proposed slots" value={data.stats.proposed} />
        <Stat label="Open cases" value={data.stats.openCases} />
      </div>

      <div className="mb-4">
        <Segmented
          value={filter}
          options={FILTERS}
          onChange={(id) => setFilter(id as (typeof FILTERS)[number]["id"])}
        />
      </div>

      <Panel className="overflow-hidden p-0">
        {rows.length === 0 ? (
          <EmptyState
            title="Nothing in this view"
            body="Capture a branded form or clear the filter."
          />
        ) : (
          <>
            <ul className="divide-y divide-border md:hidden">
              {rows.map((j) => (
                <JourneyRow key={j.id} journey={j} />
              ))}
            </ul>
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-border bg-raised/50 text-xs text-muted">
                  <tr>
                    <th className="px-4 py-2.5 font-medium">Buyer</th>
                    <th className="px-3 py-2.5 font-medium">Zone</th>
                    <th className="px-3 py-2.5 font-medium">Journey</th>
                    <th className="px-3 py-2.5 font-medium">Contact</th>
                    <th className="px-3 py-2.5 font-medium">Qualification</th>
                    <th className="px-3 py-2.5 font-medium">Consult</th>
                    <th className="px-4 py-2.5 font-medium">Updated</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {rows.map((j) => (
                    <tr key={j.id} className="hover:bg-raised/40">
                      <td className="px-4 py-3">
                        <Link
                          to="/ops/journeys/$id"
                          params={{ id: j.id }}
                          className="block min-w-40"
                        >
                          <span className="font-medium">{j.person.displayName}</span>
                          <span className="mt-0.5 block text-xs text-muted">
                            {SOURCE_LABELS[j.source] ?? j.source}
                            {j.openCases > 0 ? " · Open case" : ""}
                          </span>
                        </Link>
                      </td>
                      <td className="px-3 py-3 text-muted">{j.serviceZone ?? "—"}</td>
                      <td className="px-3 py-3">
                        <StateChip value={j.journeyState} />
                      </td>
                      <td className="px-3 py-3">
                        <StateChip value={j.contactability} />
                      </td>
                      <td className="px-3 py-3">
                        <StateChip value={j.qualificationState} />
                      </td>
                      <td className="px-3 py-3">
                        <StateChip value={j.consultationState} />
                      </td>
                      <td className="px-4 py-3 text-xs text-subtle">
                        {j.nextAppointment
                          ? `${prettyState(j.nextAppointment.state)} ${formatRelative(j.nextAppointment.startsAt)}`
                          : formatRelative(j.updatedAt)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Panel>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums tracking-tight">{value}</p>
    </div>
  );
}

function JourneyRow({ journey }: { journey: JourneyCard }) {
  return (
    <li>
      <Link
        to="/ops/journeys/$id"
        params={{ id: journey.id }}
        className="flex flex-col gap-2 px-4 py-3.5 hover:bg-raised/50"
      >
        <div className="flex flex-wrap items-center gap-2">
          <p className="truncate text-sm font-medium">{journey.person.displayName}</p>
          <StateChip value={journey.journeyState} />
          {journey.openCases > 0 ? <StateChip value="open" /> : null}
        </div>
        <p className="truncate text-xs text-muted">
          {SOURCE_LABELS[journey.source] ?? journey.source}
          {journey.serviceZone ? ` · ${journey.serviceZone}` : ""}
        </p>
        <div className="flex flex-wrap items-center gap-1.5">
          <StateChip value={journey.contactability} />
          <StateChip value={journey.qualificationState} />
          <StateChip value={journey.consultationState} />
        </div>
      </Link>
    </li>
  );
}
