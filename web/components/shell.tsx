"use client";

import {
  Activity,
  Boxes,
  FileLock,
  Gauge,
  Inbox,
  LogOut,
  MessageCircle,
  ScrollText,
  ShieldCheck,
  SlidersHorizontal,
  Users,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { ThemeToggle } from "@/components/theme-toggle";
import { Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn, humanize } from "@/lib/format";

const NAV_GROUPS = [
  {
    label: "Operate",
    items: [
      { href: "/", label: "Dashboard", icon: Gauge },
      { href: "/runs", label: "Runs", icon: Activity },
      { href: "/inbox", label: "Inbox", icon: Inbox },
    ],
  },
  {
    label: "Govern",
    items: [
      { href: "/policies", label: "Policies", icon: ScrollText },
      { href: "/controls", label: "Controls", icon: SlidersHorizontal },
      { href: "/audit", label: "Audit", icon: ShieldCheck },
    ],
  },
  {
    label: "People",
    items: [
      { href: "/employees", label: "Employees", icon: Users },
      { href: "/data-requests", label: "Data requests", icon: FileLock },
      { href: "/chat", label: "Ask Relay", icon: MessageCircle },
    ],
  },
  { label: "Build", items: [{ href: "/registry", label: "Registry", icon: Boxes }] },
];

function initials(name: string): string {
  return name
    .replace(/\(.*\)/, "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

export function Shell({ children }: { children: ReactNode }) {
  const { session, signOut } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (session === null) router.replace("/welcome");
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
      <aside className="flex shrink-0 flex-col bg-sidebar text-sidebar-foreground md:sticky md:top-0 md:h-dvh md:w-60">
        <div className="flex items-center gap-2.5 px-5 pt-5 pb-4">
          <span className="wordmark text-[26px]">relay</span>
          {api.isDemoData && (
            <span
              role="note"
              title="Demo data: these numbers are invented for building the screens, not measured from runs."
              className="ml-auto rounded-full border border-white/15 px-2 py-0.5 text-[10.5px] font-medium tracking-wide text-sidebar-muted uppercase"
            >
              Demo data
            </span>
          )}
        </div>
        <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-1 md:flex-col md:gap-5 md:overflow-y-auto md:pb-0">
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="flex gap-1 md:flex-col md:gap-0.5">
              <p className="hidden px-2 pb-1 text-[10.5px] font-semibold tracking-[0.08em] text-sidebar-muted uppercase md:block">
                {group.label}
              </p>
              {group.items.map(({ href, label, icon: Icon }) => {
                const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
                return (
                  <Link
                    key={href}
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "relative flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13.5px] whitespace-nowrap text-sidebar-foreground/75 transition-colors hover:bg-sidebar-active hover:text-sidebar-foreground",
                      active && "bg-sidebar-active font-medium text-sidebar-foreground",
                    )}
                  >
                    {active && (
                      <span className="absolute top-1.5 bottom-1.5 -left-3 hidden w-0.5 rounded-full bg-accent md:block" aria-hidden />
                    )}
                    <Icon className={cn("size-4", active ? "text-accent" : "text-sidebar-muted")} aria-hidden />
                    {label}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="flex items-center gap-3 border-t border-white/10 px-4 py-3 md:mt-auto">
          <span className="grid size-8 shrink-0 place-items-center rounded-full bg-white/10 text-xs font-semibold">
            {initials(session.user.display_name)}
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13px] font-medium">{session.user.display_name}</p>
            <p className="truncate text-[11.5px] text-sidebar-muted">
              {session.user.roles.map(humanize).join(" · ")}
            </p>
          </div>
          <ThemeToggle className="grid size-8 place-items-center rounded-md text-sidebar-muted hover:bg-sidebar-active hover:text-sidebar-foreground" />
          <button
            type="button"
            onClick={signOut}
            aria-label="Sign out"
            title="Sign out"
            className="grid size-8 place-items-center rounded-md text-sidebar-muted hover:bg-sidebar-active hover:text-sidebar-foreground"
          >
            <LogOut className="size-4" aria-hidden />
          </button>
        </div>
      </aside>
      <div className="min-w-0 flex-1">
        <main className="mx-auto max-w-6xl px-4 py-6 md:px-10 md:py-10">{children}</main>
      </div>
    </div>
  );
}
