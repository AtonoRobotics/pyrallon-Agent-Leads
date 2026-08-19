import { cn } from "@/lib/utils";

const tones = {
  neutral: "bg-raised text-muted",
  accent: "bg-accent/10 text-accent",
  ok: "bg-ok/10 text-ok",
  warn: "bg-warn/10 text-warn",
  danger: "bg-danger/10 text-danger",
  info: "bg-info/10 text-info",
} as const;

export function Badge({
  className,
  tone = "neutral",
  children,
}: {
  className?: string;
  tone?: keyof typeof tones;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm px-1.5 py-0.5 text-xs font-medium leading-4",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
