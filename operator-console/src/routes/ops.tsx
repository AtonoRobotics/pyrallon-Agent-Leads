import { Outlet, createFileRoute } from "@tanstack/react-router";
import { RedirectToSignIn } from "@/lib/auth/gates";
import { useCurrentUserState } from "@/lib/auth/use-current-user";
import { OpsShell } from "@/components/ops/shell";

export const Route = createFileRoute("/ops")({ component: OpsLayout });

function OpsLayout() {
  const { user, isPending } = useCurrentUserState();
  if (isPending) {
    return (
      <div className="min-h-dvh bg-bg">
        <div className="mx-auto max-w-5xl px-6 py-12">
          <div className="h-7 w-40 animate-pulse rounded-md bg-raised" />
          <div className="mt-6 h-48 animate-pulse rounded-lg bg-surface" />
        </div>
      </div>
    );
  }
  if (!user) return <RedirectToSignIn />;
  return (
    <OpsShell>
      <Outlet />
    </OpsShell>
  );
}
