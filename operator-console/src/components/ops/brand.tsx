import { Link } from "@tanstack/react-router";
import { cn } from "@/lib/utils";

export function Mark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "grid size-7 shrink-0 place-items-center rounded-md bg-accent text-accent-fg",
        className,
      )}
      aria-hidden
    >
      <svg viewBox="0 0 16 16" className="size-3.5" fill="currentColor">
        <path d="M3.2 2h5.15c2.2 0 3.55 1.28 3.55 3.22 0 1.95-1.38 3.28-3.58 3.28H5.7V14H3.2V2zm2.5 4.5h2.35c.95 0 1.5-.52 1.5-1.28S8.98 3.98 8.05 3.98H5.7V6.5z" />
      </svg>
    </span>
  );
}

export function BrandLockup({
  to = "/",
  subtitle,
  className,
}: {
  to?: "/";
  subtitle?: string;
  className?: string;
}) {
  return (
    <Link to={to} className={cn("flex items-center gap-2.5", className)}>
      <Mark />
      <span className="min-w-0">
        <span className="block text-sm font-semibold leading-none tracking-tight">Pyrallon</span>
        {subtitle ? (
          <span className="mt-1 block text-xs leading-none text-muted">{subtitle}</span>
        ) : null}
      </span>
    </Link>
  );
}
