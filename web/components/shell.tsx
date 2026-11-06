"use client";

import {
  Activity,
  Boxes,
  Gauge,
  Inbox,
  LogOut,
  ScrollText,
  ShieldCheck,
  SlidersHorizontal,
  Users,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { Badge, Button, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn, humanize } from "@/lib/format";

const NAV = [
  { href: "/", label: "Dashboard", icon: Gauge },
  { href: "/runs", label: "Runs", icon: Activity },
  { href: "/inbox", label: "Inbox", icon: Inbox },
  { href: "/policies", label: "Policies", icon: ScrollText },
  { href: "/controls", label: "Controls", icon: SlidersHorizontal },
  { href: "/audit", label: "Audit", icon: ShieldCheck },
  { href: "/employees", label: "Employees", icon: Users },
  { href: "/registry", label: "Registry", icon: Boxes },
];

export function Shell({ children }: { children: ReactNode }) {
  const { session, signOut } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (session === null) router.replace("/login");
  }, [session, router]);

  if (!session) {
    return (
      <div className="p-8" role="status" aria-label="Checking your session">
        <Skeleton className="h-8 w-48" />
      </div>
    );
  }

  return (
    <div className="flex min-h-dvh flex-col md:flex-row">
      <aside className="flex shrink-0 flex-col border-b border-border bg-surface md:w-56 md:border-r md:border-b-0">
        <div className="px-4 py-4 text-base font-semibold tracking-tight">Relay</div>
        <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col md:pb-0">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-2 rounded-md px-3 py-2 text-sm whitespace-nowrap hover:bg-muted",
                  active && "bg-muted font-medium",
                )}
              >
                <Icon className="size-4" aria-hidden />
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-4 py-2 md:mt-auto md:block md:p-3">
          <p className="truncate text-sm font-medium">{session.user.display_name}</p>
          <div className="mt-1 flex flex-wrap gap-1">
            {session.user.roles.map((role) => (
              <Badge key={role} tone="accent">
                {humanize(role)}
              </Badge>
            ))}
          </div>
          <Button variant="ghost" size="sm" className="md:mt-2 md:w-full md:justify-start" onClick={signOut}>
            <LogOut className="size-3.5" aria-hidden />
            Sign out
          </Button>
        </div>
      </aside>
      <div className="min-w-0 flex-1">
        {api.isDemoData && (
          <p role="note" className="border-b border-warn/30 bg-warn/10 px-4 py-2 text-sm text-warn md:px-8">
            Demo data: these numbers are invented for building the screens, not measured from runs.
          </p>
        )}
        <main className="mx-auto max-w-6xl p-4 md:p-8">{children}</main>
      </div>
    </div>
  );
}
