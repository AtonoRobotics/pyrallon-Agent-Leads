import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowRight, Calendar, ClipboardList, ListChecks } from "lucide-react";
import { useCurrentUserState } from "@/lib/auth/use-current-user";
import { BrandLockup } from "@/components/ops/brand";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/")({ component: Home });

const FEATURES = [
  {
    icon: ClipboardList,
    title: "Intake",
    body: "A branded form becomes a person, buying party, journey, and labeled assertions.",
  },
  {
    icon: ListChecks,
    title: "Qualify",
    body: "Consultation-ready is a predicate over identity, suppression, representation, and four required criteria.",
  },
  {
    icon: Calendar,
    title: "Consult",
    body: "Ready buyers get a local proposed slot. Calendar confirm is denied until a live calendar grant exists.",
  },
];

const PREVIEW_ROWS = [
  ["Elena Vasquez", "San Antonio", "Consult ready", "Contactable", "Proposed"],
  ["Marcus Hale", "Austin", "Qualifying", "Contactable", "Not ready"],
  ["Priya Shah", "Fredericksburg", "Blocked", "Suppressed", "Not ready"],
  ["James Whitaker", "San Antonio", "Qualifying", "Contactable", "Not ready"],
];

function Home() {
  const { user, isPending } = useCurrentUserState();

  return (
    <main className="flex min-h-dvh flex-col bg-bg text-fg">
      <header className="mx-auto flex w-full max-w-6xl items-center justify-between px-5 py-4 md:px-8">
        <BrandLockup subtitle="Atono Brokerage" />
        <div className="flex items-center gap-2">
          {isPending ? (
            <div className="h-10 w-24 animate-pulse rounded-md bg-raised" />
          ) : user ? (
            <Button asChild>
              <Link to="/ops">
                Open console
                <ArrowRight className="size-4" />
              </Link>
            </Button>
          ) : (
            <Button asChild>
              <Link to="/login">Sign in</Link>
            </Button>
          )}
        </div>
      </header>

      <section className="mx-auto grid w-full max-w-6xl items-center gap-10 px-5 py-8 md:grid-cols-[1fr_1.15fr] md:px-8 md:py-12">
        <div>
          <p className="text-xs font-medium text-muted">Texas residential buyer representation</p>
          <h1 className="mt-3 max-w-xl text-3xl font-semibold leading-tight tracking-tight md:text-4xl">
            Pipeline, qualification, and consults in one operator console.
          </h1>
          <p className="mt-4 max-w-lg text-sm leading-relaxed text-muted md:text-base">
            Work inbound buyer files without collapsing contact, qualification, and
            representation into a single funnel. Every item on the record stays labeled.
          </p>
          <div className="mt-7 flex flex-col gap-2 sm:flex-row">
            <Button asChild size="lg">
              <Link to={user ? "/ops" : "/login"}>
                {user ? "Continue to pipeline" : "Sign in to the console"}
                <ArrowRight className="size-4" />
              </Link>
            </Button>
            <Button asChild size="lg" variant="secondary">
              <Link to={user ? "/ops/capture" : "/login"}>Capture a lead</Link>
            </Button>
          </div>
          <p className="mt-4 text-xs text-subtle">
            Email, SMS, and calendar connectors are not activated in this environment.
          </p>
        </div>

        <div className="overflow-hidden rounded-xl border border-border bg-surface shadow-panel">
          <div className="flex items-center gap-2 border-b border-border bg-raised/70 px-3 py-2">
            <span className="size-2 rounded-full bg-border" />
            <span className="size-2 rounded-full bg-border" />
            <span className="size-2 rounded-full bg-border" />
            <p className="ml-2 text-xs text-muted">Pipeline · Atono Brokerage</p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-border text-xs text-muted">
                <tr>
                  <th className="px-4 py-2.5 font-medium">Buyer</th>
                  <th className="hidden px-3 py-2.5 font-medium sm:table-cell">Zone</th>
                  <th className="px-3 py-2.5 font-medium">Journey</th>
                  <th className="hidden px-3 py-2.5 font-medium md:table-cell">Contact</th>
                  <th className="px-3 py-2.5 font-medium">Consult</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {PREVIEW_ROWS.map((row) => (
                  <tr key={row[0]}>
                    <td className="px-4 py-2.5 font-medium">{row[0]}</td>
                    <td className="hidden px-3 py-2.5 text-muted sm:table-cell">{row[1]}</td>
                    <td className="px-3 py-2.5">
                      <Badge
                        tone={
                          row[2] === "Consult ready"
                            ? "ok"
                            : row[2] === "Blocked"
                              ? "danger"
                              : "info"
                        }
                      >
                        {row[2]}
                      </Badge>
                    </td>
                    <td className="hidden px-3 py-2.5 md:table-cell">
                      <Badge tone={row[3] === "Suppressed" ? "danger" : "ok"}>{row[3]}</Badge>
                    </td>
                    <td className="px-3 py-2.5">
                      <Badge tone={row[4] === "Proposed" ? "accent" : "neutral"}>{row[4]}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="border-t border-border bg-surface">
        <div className="mx-auto grid max-w-6xl gap-px bg-border md:grid-cols-3">
          {FEATURES.map((item) => {
            const Icon = item.icon;
            return (
              <article key={item.title} className="bg-surface px-6 py-8 md:px-8">
                <Icon className="size-5 text-accent" strokeWidth={1.5} />
                <h2 className="mt-4 text-base font-semibold tracking-tight">{item.title}</h2>
                <p className="mt-2 text-sm leading-relaxed text-muted">{item.body}</p>
              </article>
            );
          })}
        </div>
      </section>

      <footer className="border-t border-border bg-surface px-5 py-6 text-xs text-subtle md:px-8">
        <div className="mx-auto flex max-w-6xl flex-col gap-1 sm:flex-row sm:justify-between">
          <p>Pyrallon · Atono Brokerage operator surface</p>
          <p>Does not practice law. Broker policy may only narrow authority.</p>
        </div>
      </footer>
    </main>
  );
}
