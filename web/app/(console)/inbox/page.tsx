"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";

import { TASK_KIND_LABELS } from "@/components/status";
import { Badge, Button, Card, EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { humanize, slaLabel } from "@/lib/format";
import type { DecisionOutput, HumanTask, TaskKind } from "@/lib/types";

const KIND_ORDER: TaskKind[] = [
  "reserved_decision",
  "conflict",
  "approval",
  "low_confidence",
  "outage",
  "exception",
  "route_only",
];

/** A person overriding the suggestion, or saying no, must say why (PRD 16.2). */
function needsReason(task: HumanTask, option: string): boolean {
  const suggested = task.context_packet.suggested_option;
  return /reject|override|refer/.test(option) || (suggested !== undefined && option !== suggested);
}

const RESIGNATION = "employment.resignation.accept";

/** Optional terms a manager may agree when accepting a resignation (exit.v1 reads them). */
function ResignationTerms({
  id,
  releaseDate,
  buyout,
  onReleaseDate,
  onBuyout,
}: {
  id: string;
  releaseDate: string;
  buyout: boolean;
  onReleaseDate: (value: string) => void;
  onBuyout: (value: boolean) => void;
}) {
  return (
    <fieldset className="mt-3 rounded-md border border-border p-3">
      <legend className="px-1 text-sm">Agreed terms (optional)</legend>
      <label className="block text-sm" htmlFor={`release-${id}`}>
        Release date
      </label>
      <input
        id={`release-${id}`}
        type="date"
        value={releaseDate}
        disabled={buyout}
        onChange={(event) => onReleaseDate(event.target.value)}
        className="mt-1 rounded-md border border-border bg-background p-1.5 text-sm"
      />
      <label className="mt-2 flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={buyout}
          disabled={releaseDate !== ""}
          onChange={(event) => onBuyout(event.target.checked)}
        />
        Buyout of the notice period agreed
      </label>
      <p className="mt-1 text-xs text-muted-foreground">
        Leave both empty to follow the notice period. A release date and a buyout cannot both be set.
      </p>
    </fieldset>
  );
}

function TaskCard({ task, now }: { task: HumanTask; now: number }) {
  const client = useQueryClient();
  const { hasRole } = useAuth();
  const [option, setOption] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [releaseDate, setReleaseDate] = useState("");
  const [buyout, setBuyout] = useState(false);
  const isResignation = task.action === RESIGNATION;
  const decide = useMutation({
    mutationFn: (args: { option: string; reason: string }) => {
      const output: DecisionOutput = {};
      if (isResignation && releaseDate) output.release_date = releaseDate;
      if (isResignation && buyout) output.buyout_agreed = true;
      return api.decideTask(task.id, args.option, args.reason, output);
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["tasks"] });
      void client.invalidateQueries({ queryKey: ["runs"] });
    },
  });
  const sla = slaLabel(task.sla_due_at, now);
  const canDecide = hasRole(task.assignee_role);
  const reasonRequired = option !== null && needsReason(task, option);
  const packet = task.context_packet;

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="font-medium">{task.title}</h3>
        <Badge tone={sla.overdue ? "bad" : "neutral"}>{sla.text}</Badge>
      </div>
      <p className="mt-2 text-sm">{packet.summary}</p>
      {packet.likely_cause && (
        <p className="mt-1 text-sm text-muted-foreground">Likely cause: {packet.likely_cause}</p>
      )}
      <dl className="mt-3 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
        {Object.entries(packet.facts).map(([label, value]) => (
          <div key={label} className="flex justify-between gap-3 border-b border-border pb-1">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="text-right font-medium">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-xs text-muted-foreground">
        {packet.rule && <>Triggered by {packet.rule} · </>}
        For {humanize(task.assignee_role)}
        {task.run_id && (
          <>
            {" · "}
            <Link href={`/runs/${task.run_id}`} className="underline">
              View run
            </Link>
          </>
        )}
      </p>

      {canDecide ? (
        <form
          className="mt-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (option) decide.mutate({ option, reason });
          }}
        >
          <div className="flex flex-wrap gap-2" role="group" aria-label={`Options for ${task.title}`}>
            {task.options.map((candidate) => (
              <Button
                key={candidate}
                size="sm"
                variant={option === candidate ? "primary" : "secondary"}
                aria-pressed={option === candidate}
                onClick={() => setOption(candidate)}
              >
                {humanize(candidate)}
                {packet.suggested_option === candidate && " (suggested)"}
              </Button>
            ))}
          </div>
          {option && isResignation && (
            <ResignationTerms
              id={task.id}
              releaseDate={releaseDate}
              buyout={buyout}
              onReleaseDate={setReleaseDate}
              onBuyout={setBuyout}
            />
          )}
          {option && (
            <div className="mt-3">
              <label className="block text-sm" htmlFor={`reason-${task.id}`}>
                Reason{reasonRequired ? "" : " (optional)"}
              </label>
              <textarea
                id={`reason-${task.id}`}
                rows={2}
                value={reason}
                required={reasonRequired}
                onChange={(event) => setReason(event.target.value)}
                className="mt-1 w-full rounded-md border border-border bg-background p-2 text-sm"
              />
              <Button
                type="submit"
                variant="primary"
                size="sm"
                className="mt-2"
                disabled={decide.isPending || (reasonRequired && !reason.trim())}
              >
                Record decision: {humanize(option)}
              </Button>
            </div>
          )}
          {decide.isError && (
            <p role="alert" className="mt-2 text-sm text-danger">
              {errorMessage(decide.error)}
            </p>
          )}
        </form>
      ) : (
        <p className="mt-3 text-sm text-muted-foreground">
          Only someone with the {humanize(task.assignee_role)} role can decide this.
        </p>
      )}
    </Card>
  );
}

export default function InboxPage() {
  const tasks = useQuery({ queryKey: ["tasks"], queryFn: () => api.tasks(), refetchInterval: 3_000 });
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);

  return (
    <>
      <PageHeader title="Inbox" description="Decisions and exceptions waiting for a person, with the context to make them." />
      {tasks.isPending && <LoadingState label="Loading tasks" />}
      {tasks.isError && <ErrorState error={tasks.error} onRetry={() => tasks.refetch()} />}
      {tasks.data?.length === 0 && (
        <EmptyState title="Nothing waiting" description="New decisions and exceptions will appear here." />
      )}
      {tasks.data && tasks.data.length > 0 && (
        <div className="space-y-6">
          {KIND_ORDER.map((kind) => {
            const group = tasks.data.filter((task) => task.kind === kind);
            if (group.length === 0) return null;
            return (
              <section key={kind} aria-label={TASK_KIND_LABELS[kind]}>
                <h2 className="mb-2 text-sm font-medium text-muted-foreground">
                  {TASK_KIND_LABELS[kind]} ({group.length})
                </h2>
                <div className="space-y-3">
                  {group.map((task) => (
                    <TaskCard key={task.id} task={task} now={now} />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </>
  );
}
