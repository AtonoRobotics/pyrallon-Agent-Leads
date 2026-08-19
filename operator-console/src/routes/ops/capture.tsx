import { useNavigate, createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import { captureLead } from "@/lib/ops/server";
import { PageHeader, Panel } from "@/components/ops/shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

export const Route = createFileRoute("/ops/capture")({ component: CapturePage });

const ZONES = ["San Antonio", "Austin", "Fredericksburg"] as const;

function CapturePage() {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    fullName: "",
    email: "",
    phone: "",
    zone: "San Antonio",
    intent: "",
    budget: "",
    timing: "",
    message: "",
  });

  function set<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const res = await captureLead({
        data: { ...form, channel: "form" },
      });
      toast.success("Inbound form admitted. No outbound send.");
      await navigate({ to: "/ops/journeys/$id", params: { id: res.journeyId } });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Capture denied");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Capture"
        description="Admit an inbound form. Email and SMS ingress are not activated. Nothing is sent."
      />
      <Panel className="max-w-2xl">
        <form className="space-y-4" onSubmit={(e) => void onSubmit(e)}>
          <Field label="Full name">
            <Input required value={form.fullName} onChange={(e) => set("fullName", e.target.value)} />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Email">
              <Input type="email" value={form.email} onChange={(e) => set("email", e.target.value)} />
            </Field>
            <Field label="Mobile">
              <Input value={form.phone} onChange={(e) => set("phone", e.target.value)} />
            </Field>
          </div>
          <Field label="Service zone">
            <Select value={form.zone} onChange={(e) => set("zone", e.target.value)}>
              {ZONES.map((z) => (
                <option key={z}>{z}</option>
              ))}
            </Select>
          </Field>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Intent">
              <Input value={form.intent} onChange={(e) => set("intent", e.target.value)} />
            </Field>
            <Field label="Budget (stated)">
              <Input value={form.budget} onChange={(e) => set("budget", e.target.value)} />
            </Field>
            <Field label="Timing">
              <Input value={form.timing} onChange={(e) => set("timing", e.target.value)} />
            </Field>
          </div>
          <Field label="Inbound message">
            <Textarea required value={form.message} onChange={(e) => set("message", e.target.value)} />
          </Field>
          <Button type="submit" disabled={busy}>
            Admit inbound form
          </Button>
        </form>
      </Panel>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <Label>{label}</Label>
      {children}
    </label>
  );
}
