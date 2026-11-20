"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, Lock, Mail } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Button, ErrorState, LoadingState } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { humanize } from "@/lib/format";

const POINTS = [
  "Agents close the loops across IT, Admin and Finance",
  "Reserved decisions always go to a named person",
  "Every action policy-checked, audited and costed in INR",
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

export default function LoginPage() {
  const { session, signIn } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const users = useQuery({ queryKey: ["demo-users"], queryFn: () => api.demoUsers() });
  const login = useMutation({
    mutationFn: (address: string) => api.login(address),
    onSuccess: signIn,
  });

  useEffect(() => {
    if (session) router.replace("/");
  }, [session, router]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (email.trim()) login.mutate(email.trim().toLowerCase());
  };

  return (
    <main className="grid min-h-dvh lg:grid-cols-2">
      <aside className="relative hidden flex-col bg-[radial-gradient(60rem_40rem_at_120%_-10%,rgb(63_185_160/0.18),transparent_60%)] justify-between overflow-hidden bg-sidebar p-10 text-sidebar-foreground lg:flex">
        <Link href="/welcome" className="wordmark text-[30px]">
          relay
        </Link>
        <div>
          <h2 className="max-w-md font-serif text-5xl leading-[1.05] font-normal text-balance">
            HR work that runs itself, with people on the decisions that matter.
          </h2>
          <ul className="mt-6 space-y-3 text-sidebar-foreground/80">
            {POINTS.map((point) => (
              <li key={point} className="flex gap-3">
                <span className="mt-2 size-1.5 shrink-0 rounded-full bg-accent" aria-hidden />
                {point}
              </li>
            ))}
          </ul>
        </div>
        <p className="text-sm text-sidebar-muted">TBO · VoyageHack 4.0 · synthetic data only</p>
      </aside>

      <section className="flex flex-col justify-center px-4 py-10 sm:px-10">
        <div className="mx-auto w-full max-w-sm">
          <Link
            href="/welcome"
            className="mb-8 inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline lg:hidden"
          >
            <ArrowLeft className="size-3.5" aria-hidden /> About Relay
          </Link>
          <h1 className="text-4xl">Sign in</h1>
          <p className="mt-1 text-sm text-muted-foreground">Use your work account to open the HR console.</p>

          <form className="mt-6 space-y-4" onSubmit={submit}>
            <div>
              <label htmlFor="email" className="text-sm font-medium">
                Work email
              </label>
              <div className="relative mt-1.5">
                <Mail className="pointer-events-none absolute top-2.5 left-3 size-4 text-muted-foreground" aria-hidden />
                <input
                  id="email"
                  type="email"
                  autoComplete="username"
                  required
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="name@relay.test"
                  className="w-full rounded-md border border-border bg-surface py-2 pr-3 pl-9 text-sm"
                />
              </div>
            </div>
            <div>
              <label htmlFor="password" className="text-sm font-medium">
                Password
              </label>
              <div className="relative mt-1.5">
                <Lock className="pointer-events-none absolute top-2.5 left-3 size-4 text-muted-foreground" aria-hidden />
                <input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  className="w-full rounded-md border border-border bg-surface py-2 pr-3 pl-9 text-sm"
                />
              </div>
              <p className="mt-1.5 text-xs text-muted-foreground">
                Demo environment: accounts are checked by email only, so any password works.
              </p>
            </div>
            <Button type="submit" variant="primary" className="w-full" disabled={login.isPending || !email.trim()}>
              {login.isPending ? "Signing in…" : "Sign in"}
            </Button>
            {login.isError && (
              <p role="alert" className="text-sm text-danger">
                {errorMessage(login.error)}
              </p>
            )}
          </form>

          <div className="mt-8">
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              <span className="h-px flex-1 bg-border" />
              or use a demo account
              <span className="h-px flex-1 bg-border" />
            </div>
            {users.isPending && <LoadingState label="Loading demo accounts" rows={3} />}
            {users.isError && <ErrorState error={users.error} onRetry={() => users.refetch()} />}
            {users.data && (
              <ul className="mt-3 grid gap-2">
                {users.data.map((user) => (
                  <li key={user.email}>
                    <button
                      type="button"
                      disabled={login.isPending}
                      aria-label={`Sign in as ${user.display_name}`}
                      onClick={() => {
                        setEmail(user.email);
                        login.mutate(user.email);
                      }}
                      className="flex w-full items-center gap-3 rounded-md border border-border bg-surface p-2.5 text-left hover:bg-muted disabled:opacity-60"
                    >
                      <span className="grid size-8 shrink-0 place-items-center rounded-full bg-accent/12 text-xs font-semibold text-accent">
                        {initials(user.display_name)}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium">{user.display_name}</span>
                        <span className="block truncate text-xs text-muted-foreground">
                          {user.roles.map(humanize).join(" · ")}
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </section>
    </main>
  );
}
