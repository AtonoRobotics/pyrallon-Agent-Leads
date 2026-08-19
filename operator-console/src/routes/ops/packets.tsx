import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { getPacketStatus } from "@/lib/ops/server";
import { PageHeader, Panel, SectionLabel } from "@/components/ops/shell";
import { Badge } from "@/components/ui/badge";

export const Route = createFileRoute("/ops/packets")({ component: PacketsPage });

type Payload = Awaited<ReturnType<typeof getPacketStatus>>;

function toneFor(status: string): "ok" | "warn" | "danger" | "info" | "neutral" {
  if (status === "complete" || status === "pass" || status === "active") return "ok";
  if (status === "complete_in_process" || status === "fail_closed") return "info";
  if (status === "blocked" || status === "inactive") return "warn";
  if (status === "prohibited" || status === "fail") return "danger";
  return "neutral";
}

function PacketsPage() {
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPacketStatus()
      .then(setData)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Failed to load"));
  }, []);

  if (error) return <p className="text-sm text-danger">{error}</p>;
  if (!data) return <div className="h-64 animate-pulse rounded-lg bg-surface" />;

  return (
    <div>
      <PageHeader
        title="Packets"
        description="PKT-00 through PKT-10 against the governing contracts. Fail-closed items are listed as blocked, not as live providers."
        meta={`${data.tenant.brokerageName} · Habitat ${data.activation.habitat} · Temporal ${data.activation.temporal.replaceAll("_", " ")}`}
      />

      <div className="mb-5 grid gap-3 sm:grid-cols-4">
        <Stat label="Habitat" value="In-process" />
        <Stat label="Form connector" value="Active" />
        <Stat label="Email / SMS / calendar" value="Inactive" />
        <Stat label="Temporal Cloud" value="Not configured" />
      </div>

      <Panel className="mb-5 overflow-x-auto p-0">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-border bg-raised/60 text-xs text-muted">
            <tr>
              <th className="px-4 py-2.5 font-medium">Packet</th>
              <th className="px-4 py-2.5 font-medium">Status</th>
              <th className="px-4 py-2.5 font-medium">What is running</th>
            </tr>
          </thead>
          <tbody>
            {data.packets.map((pkt) => (
              <tr key={pkt.id} className="border-b border-border last:border-0">
                <td className="whitespace-nowrap px-4 py-3 align-top">
                  <p className="font-mono text-xs text-muted">{pkt.id}</p>
                  <p className="font-medium">{pkt.name}</p>
                </td>
                <td className="px-4 py-3 align-top">
                  <Badge tone={toneFor(pkt.status)}>{pkt.status.replaceAll("_", " ")}</Badge>
                </td>
                <td className="px-4 py-3 align-top text-muted">{pkt.summary}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel>
          <SectionLabel>Connector inventory</SectionLabel>
          <ul className="mt-3 divide-y divide-border">
            {data.connectors.map((c) => (
              <li key={c.connectorId} className="flex items-start justify-between gap-3 py-2.5">
                <div>
                  <p className="text-sm font-medium">{c.connectorId}</p>
                  <p className="text-xs text-muted">{c.notes}</p>
                </div>
                <Badge tone={toneFor(c.status)}>{c.status}</Badge>
              </li>
            ))}
          </ul>
        </Panel>
        <Panel>
          <SectionLabel>Gate evidence</SectionLabel>
          <ul className="mt-3 divide-y divide-border">
            {data.gates.map((g) => (
              <li key={g.gateId} className="py-2.5">
                <div className="flex items-center justify-between gap-3">
                  <p className="font-mono text-xs">{g.gateId}</p>
                  <Badge tone={toneFor(g.status)}>{g.status}</Badge>
                </div>
                <p className="mt-1 text-xs text-muted">{g.notes}</p>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-1 text-sm font-medium">{value}</p>
    </div>
  );
}
