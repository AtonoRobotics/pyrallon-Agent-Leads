import { Link, useRouterState } from "@tanstack/react-router";
import { Calendar, Inbox, LayoutList, ShieldAlert, UserPlus, Waypoints } from "lucide-react";
import { signOut } from "@/lib/auth/client";
import { useCurrentUserState } from "@/lib/auth/use-current-user";
import { BrandLockup, Mark } from "@/components/ops/brand";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/ops", label: "Pipeline", icon: LayoutList, exact: true },
  { to: "/ops/exceptions", label: "Exceptions", icon: ShieldAlert, exact: false },
  { to: "/ops/consults", label: "Consults", icon: Calendar, exact: false },
  { to: "/ops/capture", label: "Capture", icon: UserPlus, exact: false },
  { to: "/ops/packets", label: "Packets", icon: Waypoints, exact: false },
] as const;

export function OpsShell({ children }: { children: React.ReactNode }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { user, isPending } = useCurrentUserState();
  const label = user?.displayName ?? user?.primaryEmail ?? "Account";

  return (
    <div className="min-h-dvh bg-bg text-fg">
      <aside className="fixed inset-y-0 left-0 z-20 hidden w-56 flex-col border-r border-border bg-surface md:flex">
        <div className="px-4 py-4">
          <BrandLockup subtitle="Buyer operations" />
        </div>
        <nav className="flex flex-1 flex-col gap-0.5 px-2">
          {NAV.map((item) => {
            const active = item.exact
              ? pathname === item.to
              : pathname.startsWith(item.to);
            const Icon = item.icon;
            return (
              <Link
                key={item.to}
                to={item.to}
                className={cn(
                  "flex h-9 items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors duration-150",
                  active
                    ? "bg-raised font-medium text-fg"
                    : "text-muted hover:bg-raised/80 hover:text-fg",
                )}
              >
                <Icon className="size-4" strokeWidth={1.75} />
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="border-t border-border px-3 py-3">
          {isPending ? (
            <div className="h-9 w-full animate-pulse rounded-md bg-raised" />
          ) : (
            <div className="flex items-center gap-2.5">
              {user?.profileImageUrl ? (
                <img
                  src={user.profileImageUrl}
                  alt=""
                  className="size-8 rounded-md object-cover"
                />
              ) : (
                <span className="grid size-8 place-items-center rounded-md bg-raised text-xs font-medium">
                  {label.charAt(0).toUpperCase()}
                </span>
              )}
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{label}</p>
                <button
                  type="button"
                  onClick={() => void signOut()}
                  className="text-xs text-muted hover:text-fg"
                >
                  Sign out
                </button>
              </div>
            </div>
          )}
        </div>
      </aside>

      <header className="sticky top-0 z-20 flex items-center justify-between border-b border-border bg-surface px-4 py-2.5 md:hidden">
        <Link to="/" className="flex items-center gap-2">
          <Mark className="size-6" />
          <span className="text-sm font-semibold">Pyrallon</span>
        </Link>
        {isPending ? (
          <div className="h-8 w-8 animate-pulse rounded-md bg-raised" />
        ) : (
          <button type="button" onClick={() => void signOut()} className="text-xs text-muted">
            Sign out
          </button>
        )}
      </header>

      <main className="md:pl-56">
        <div className="flex items-center gap-2 border-b border-border bg-surface px-4 py-2 text-xs text-muted md:px-6">
          <span className="size-1.5 shrink-0 rounded-full bg-warn" />
          <p>
            In-process Habitat is on. Email, SMS, calendar writes, and Temporal Cloud are not activated.
          </p>
        </div>
        <div className="mx-auto max-w-6xl px-4 py-5 pb-24 md:px-6 md:py-6 md:pb-10">
          {children}
        </div>
      </main>

      <nav className="fixed inset-x-0 bottom-0 z-20 grid grid-cols-5 border-t border-border bg-surface pb-[env(safe-area-inset-bottom)] md:hidden">
        {NAV.map((item) => {
          const active = item.exact ? pathname === item.to : pathname.startsWith(item.to);
          const Icon = item.icon;
          return (
            <Link
              key={item.to}
              to={item.to}
              className={cn(
                "flex h-14 flex-col items-center justify-center gap-1 text-[11px]",
                active ? "font-medium text-fg" : "text-muted",
              )}
            >
              <Icon className="size-4" strokeWidth={1.75} />
              {item.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}

export function PageHeader({
  title,
  description,
  meta,
  action,
}: {
  title: string;
  description?: string;
  meta?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description ? (
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted">{description}</p>
        ) : null}
        {meta ? <p className="mt-1 text-xs text-subtle">{meta}</p> : null}
      </div>
      {action}
    </div>
  );
}

export function Panel({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={cn("rounded-lg border border-border bg-surface p-4", className)}>
      {children}
    </section>
  );
}

export function SectionLabel({ children }: { children: React.ReactNode }) {
  return <p className="text-xs font-medium text-muted">{children}</p>;
}

export function EmptyState({
  icon: Icon = Inbox,
  title,
  body,
}: {
  icon?: typeof Inbox;
  title: string;
  body: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
      <Icon className="mb-3 size-6 text-subtle" strokeWidth={1.5} />
      <p className="text-sm font-medium">{title}</p>
      <p className="mt-1 max-w-sm text-sm text-muted">{body}</p>
    </div>
  );
}

export function Segmented({
  value,
  options,
  onChange,
}: {
  value: string;
  options: readonly { id: string; label: string }[];
  onChange: (id: string) => void;
}) {
  return (
    <div className="inline-flex rounded-md bg-raised p-0.5">
      {options.map((opt) => (
        <button
          key={opt.id}
          type="button"
          onClick={() => onChange(opt.id)}
          className={cn(
            "h-8 rounded-sm px-3 text-xs font-medium transition-colors duration-150",
            value === opt.id ? "bg-surface text-fg shadow-border" : "text-muted hover:text-fg",
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

export function UnderlineTabs<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: readonly { id: T; label: string }[];
  onChange: (id: T) => void;
}) {
  return (
    <div className="flex gap-4 border-b border-border">
      {options.map((opt) => (
        <button
          key={opt.id}
          type="button"
          onClick={() => onChange(opt.id)}
          className={cn(
            "-mb-px border-b-2 pb-2 text-sm transition-colors duration-150",
            value === opt.id
              ? "border-fg font-medium text-fg"
              : "border-transparent text-muted hover:text-fg",
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
