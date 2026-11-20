import { ArrowRight, BadgeIndianRupee, CheckCircle2, ScrollText, ShieldCheck, UserCheck, Workflow } from "lucide-react";
import Link from "next/link";

// What a visitor sees before signing in: what Relay does and how, in one screen.

const PILLARS = [
  {
    icon: Workflow,
    title: "Agents close the loops",
    body: "A new hire starts one run. Relay asks IT for the laptop, Admin for the access card and Finance for payroll, and chases each one. No emails, no follow-ups.",
  },
  {
    icon: UserCheck,
    title: "People make the decisions that matter",
    body: "Offers, probation, resignations and full-and-final settlements go to a named person. An agent can never approve them, whatever it is told.",
  },
  {
    icon: ScrollText,
    title: "Rules live in policy, not prompts",
    body: "Labour rules, approval limits and reserved decisions are versioned YAML that HR edits, tests and activates. Every change is on the record.",
  },
  {
    icon: ShieldCheck,
    title: "Every action is audited",
    body: "Who or what acted, under which policy version and model, with personal data masked before any model sees it. The log is hash-chained.",
  },
  {
    icon: BadgeIndianRupee,
    title: "Costed in rupees",
    body: "Model and infrastructure cost is measured per step and per onboarding, so the platform runs at a known cost per task.",
  },
  {
    icon: CheckCircle2,
    title: "Fails safely",
    body: "When a system is down, data conflicts or an agent is unsure, the work goes to a person with the context attached. Retries never order two laptops.",
  },
];

const JOURNEY = [
  "Offer accepted",
  "Documents & consent",
  "Identity checked",
  "Laptop, card, email, payroll",
  "Manager & buddy briefed",
  "Day one ready",
];

export default function WelcomePage() {
  return (
    <main className="min-h-dvh">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-4 py-5 sm:px-6">
        <span className="flex items-center gap-2 text-lg font-semibold tracking-tight">
          <span className="grid size-8 place-items-center rounded-lg bg-accent text-sm font-bold text-accent-foreground">
            R
          </span>
          Relay
        </span>
        <Link
          href="/login"
          className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm font-medium hover:bg-muted"
        >
          Sign in
        </Link>
      </header>

      <section className="mx-auto max-w-6xl px-4 pt-10 pb-14 sm:px-6 sm:pt-16">
        <p className="text-sm font-medium text-accent">AI-native HR services for TBO</p>
        <h1 className="mt-3 max-w-3xl text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
          HR work that runs itself, with people on the decisions that matter.
        </h1>
        <p className="mt-5 max-w-2xl text-lg text-muted-foreground text-pretty">
          Relay replaces the HRMS that only records events. Agents do the routine work end to end across
          IT, Admin, Finance and the hiring manager, while every action is policy-checked, audited and costed.
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <Link
            href="/login"
            className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2.5 text-sm font-medium text-accent-foreground hover:opacity-90"
          >
            Sign in to the console <ArrowRight className="size-4" aria-hidden />
          </Link>
          <a
            href="#how"
            className="inline-flex items-center rounded-md border border-border bg-surface px-4 py-2.5 text-sm font-medium hover:bg-muted"
          >
            How it works
          </a>
        </div>

        <ol
          aria-label="The onboarding journey Relay runs"
          className="mt-12 grid gap-2 sm:grid-cols-3 lg:grid-cols-6"
        >
          {JOURNEY.map((step, index) => (
            <li key={step} className="rounded-lg border border-border bg-surface p-3">
              <span className="text-xs text-muted-foreground tabular-nums">Step {index + 1}</span>
              <p className="mt-1 text-sm font-medium">{step}</p>
            </li>
          ))}
        </ol>
      </section>

      <section id="how" className="border-t border-border bg-surface/60">
        <div className="mx-auto max-w-6xl px-4 py-14 sm:px-6">
          <h2 className="text-2xl font-semibold tracking-tight">How Relay works</h2>
          <p className="mt-2 max-w-2xl text-muted-foreground">
            A platform, not a point solution. Onboarding runs in depth, and the exit journey was added
            through workflow, policy and rule files alone, with no change to the core.
          </p>
          <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {PILLARS.map(({ icon: Icon, title, body }) => (
              <article key={title} className="rounded-lg border border-border bg-surface p-5">
                <Icon className="size-5 text-accent" aria-hidden />
                <h3 className="mt-3 font-medium">{title}</h3>
                <p className="mt-1.5 text-sm text-muted-foreground">{body}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <footer className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-8 text-sm text-muted-foreground sm:px-6">
        <span>Relay · VoyageHack 4.0 · synthetic data only</span>
        <Link href="/login" className="font-medium text-foreground hover:underline">
          Sign in
        </Link>
      </footer>
    </main>
  );
}
