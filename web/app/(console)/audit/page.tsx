"use client";

import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, ShieldAlert } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Badge, Button, Card, EmptyState, ErrorState, LoadingState, PageHeader, type Tone } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { AuditEvent, AuditFilters } from "@/lib/types";

const PAGE_SIZE = 50;

function effectTone(effect: string | null): Tone {
  if (effect === "ALLOW") return "good";
  if (effect === "DENY") return "bad";
  return effect ? "warn" : "neutral";
}

function ChainStatus() {
  const check = useQuery({ queryKey: ["audit-verify"], queryFn: () => api.verifyAudit(), enabled: false });
  return (
    <div className="flex flex-wrap items-center gap-3">
      {check.data?.ok && (
        <span role="status" className="flex items-center gap-1.5 text-sm text-good">
          <CheckCircle2 className="size-4" aria-hidden />
          Chain intact: {check.data.checked.toLocaleString("en-IN")} events
        </span>
      )}
      {check.data && !check.data.ok && (
        <span role="alert" className="flex items-center gap-1.5 text-sm text-danger">
          <ShieldAlert className="size-4" aria-hidden />
          Chain broken at event {check.data.first_bad_seq}: {check.data.problem?.replaceAll("_", " ")}
        </span>
      )}
      {check.isError && (
        <span role="alert" className="text-sm text-danger">
          The check could not run.
        </span>
      )}
      <Button size="sm" disabled={check.isFetching} onClick={() => check.refetch()}>
        {check.isFetching ? "Checking…" : "Verify chain"}
      </Button>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-sm break-words">{value ?? "—"}</dd>
    </div>
  );
}

function EventDetail({ id }: { id: string }) {
  const detail = useQuery({ queryKey: ["audit-event", id], queryFn: () => api.auditEvent(id) });
  if (detail.isPending) return <LoadingState label="Loading event" rows={3} />;
  if (detail.isError) return <ErrorState error={detail.error} onRetry={() => detail.refetch()} />;
  const event = detail.data;
  return (
    <div className="space-y-4">
      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Fact label="On behalf of" value={event.on_behalf_of} />
        <Fact label="Policy version" value={event.policy_version} />
        <Fact label="Workflow version" value={event.workflow_def_version} />
        <Fact label="Skill version" value={event.skill_version} />
        <Fact label="Prompt version" value={event.prompt_version} />
        <Fact label="Model" value={event.model_id} />
        <Fact label="Inputs hash" value={event.input_hash} />
        <Fact label="Hash" value={event.hash} />
        <Fact label="Previous hash" value={event.prev_hash} />
      </dl>
      {event.reasoning && (
        <div>
          <h4 className="text-xs text-muted-foreground">Why</h4>
          <p className="mt-0.5 text-sm">{event.reasoning}</p>
        </div>
      )}
      <div>
        <h4 className="text-sm font-medium">What left our system</h4>
        {event.payload ? (
          <>
            <p className="mt-1 text-xs text-muted-foreground">
              Exactly what was {event.payload.direction === "out" ? "sent to" : "received from"} {event.payload.provider}
              {event.payload.region ? ` (${event.payload.region})` : ""}, with personal data already replaced by tokens.
            </p>
            <pre className="mt-2 overflow-x-auto rounded-md border border-border bg-background p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap">
              {event.payload.masked_text}
            </pre>
          </>
        ) : (
          <p className="mt-1 text-sm text-muted-foreground">Nothing: this event involved no external model.</p>
        )}
      </div>
    </div>
  );
}

export default function AuditPage() {
  const [draft, setDraft] = useState<AuditFilters>({});
  const [filters, setFilters] = useState<AuditFilters>({});
  const [open, setOpen] = useState<string | null>(null);
  const events = useQuery({ queryKey: ["audit", filters], queryFn: () => api.auditEvents(filters) });

  const apply = (event: FormEvent) => {
    event.preventDefault();
    setOpen(null);
    setFilters({ ...draft, before_seq: undefined });
  };
  const list: AuditEvent[] = events.data ?? [];
  const oldest = list.length === PAGE_SIZE ? list[list.length - 1].seq : null;

  return (
    <>
      <PageHeader
        title="Audit log"
        description="Everything anyone or anything did, in an append-only chain. Open an event to see why it happened."
        actions={<ChainStatus />}
      />
      <form onSubmit={apply} className="mb-4 flex flex-wrap items-end gap-2">
        {(
          [
            ["actor", "Actor"],
            ["action", "Action contains"],
            ["run_id", "Run ID"],
          ] as const
        ).map(([key, label]) => (
          <label key={key} className="text-sm">
            {label}
            <input
              value={draft[key] ?? ""}
              onChange={(e) => setDraft({ ...draft, [key]: e.target.value || undefined })}
              className="mt-1 block h-9 rounded-md border border-border bg-surface px-2 text-sm"
            />
          </label>
        ))}
        <label className="text-sm">
          Effect
          <select
            value={draft.effect ?? ""}
            onChange={(e) => setDraft({ ...draft, effect: e.target.value || undefined })}
            className="mt-1 block h-9 rounded-md border border-border bg-surface px-2 text-sm"
          >
            <option value="">Any</option>
            <option value="ALLOW">Allow</option>
            <option value="REQUIRE_HUMAN">Require human</option>
            <option value="DENY">Deny</option>
          </select>
        </label>
        <Button type="submit">Search</Button>
      </form>

      {events.isPending && <LoadingState label="Loading audit events" />}
      {events.isError && <ErrorState error={events.error} onRetry={() => events.refetch()} />}
      {events.data?.length === 0 && (
        <EmptyState title="No events match" description="Try fewer filters, or clear them to see everything." />
      )}
      {list.length > 0 && (
        <Card className="divide-y divide-border">
          {list.map((event) => (
            <div key={event.id}>
              <button
                type="button"
                aria-expanded={open === event.id}
                onClick={() => setOpen(open === event.id ? null : event.id)}
                className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 text-left hover:bg-muted/50"
              >
                <span className="w-12 text-xs text-muted-foreground tabular-nums">#{event.seq}</span>
                <span className="font-mono text-xs break-all">{event.action}</span>
                {event.effect && <Badge tone={effectTone(event.effect)}>{event.effect}</Badge>}
                {event.model_id && <Badge tone="accent">Model call</Badge>}
                <span className="ml-auto text-xs text-muted-foreground">
                  {event.actor_id} · {dateTime(event.ts)}
                </span>
              </button>
              {open === event.id && (
                <div className="border-t border-border bg-background/50 p-4">
                  <EventDetail id={event.id} />
                </div>
              )}
            </div>
          ))}
        </Card>
      )}
      {oldest !== null && (
        <div className="mt-3">
          <Button size="sm" onClick={() => setFilters({ ...filters, before_seq: oldest })}>
            Older events
          </Button>
        </div>
      )}
    </>
  );
}
