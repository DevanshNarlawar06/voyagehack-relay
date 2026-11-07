"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { RunDag } from "@/components/run-dag";
import { RunStatusBadge, StepStatusBadge } from "@/components/status";
import { Badge, Card, ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, humanize, inr } from "@/lib/format";
import type { RunLink, Step } from "@/lib/types";

function Field({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-sm break-words">{value ?? "—"}</dd>
    </div>
  );
}

function StepPanel({ step }: { step: Step }) {
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-medium">{humanize(step.step_id)}</h2>
        <StepStatusBadge status={step.status} />
        {step.human_touched && <Badge tone="warn">Human touch</Badge>}
      </div>
      <dl className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="Kind" value={step.kind} />
        <Field label="Action" value={step.action} />
        <Field label="Attempts" value={String(step.attempt)} />
        <Field label="Policy decision" value={step.decision?.effect ?? null} />
        <Field label="Why" value={step.decision?.reason ?? null} />
        <Field label="Rules" value={step.decision?.rule_ids.join(", ") ?? null} />
        <Field label="Policy version" value={step.decision?.policy_version ?? null} />
        <Field label="Model" value={step.model_id} />
        <Field label="Prompt version" value={step.prompt_version} />
        <Field label="Cost" value={inr(step.cost_inr, 4)} />
        <Field label="Started" value={dateTime(step.started_at)} />
        <Field label="Finished" value={dateTime(step.finished_at)} />
      </dl>
      {step.error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {step.error}
        </p>
      )}
    </Card>
  );
}

function LinkedRun({ label, run }: { label: string; run: RunLink }) {
  return (
    <li className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-xs text-muted-foreground">{label}</span>
      <Link href={`/runs/${run.id}`} className="font-medium hover:underline">
        {humanize(run.definition_name)}
      </Link>
      <span className="text-xs text-muted-foreground">{run.subject_label}</span>
      <RunStatusBadge status={run.status} />
    </li>
  );
}

function LinkedRuns({ parent, kids: started }: { parent?: RunLink | null; kids?: RunLink[] }) {
  const kids = started ?? [];
  if (!parent && kids.length === 0) return null;
  return (
    <Card className="p-4">
      <h2 className="text-sm font-medium">Linked runs</h2>
      <ul className="mt-3 space-y-2">
        {parent && <LinkedRun label="Started by" run={parent} />}
        {kids.map((child) => (
          <LinkedRun key={child.id} label="Started" run={child} />
        ))}
      </ul>
    </Card>
  );
}

export default function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [selected, setSelected] = useState<string | null>(null);
  const run = useQuery({ queryKey: ["run", id], queryFn: () => api.run(id), refetchInterval: 2_000 });

  const back = (
    <Link href="/runs" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline">
      <ArrowLeft className="size-3.5" aria-hidden />
      All runs
    </Link>
  );

  if (run.isPending) {
    return (
      <>
        {back}
        <LoadingState label="Loading run" rows={6} />
      </>
    );
  }
  if (run.isError) {
    return (
      <>
        {back}
        <ErrorState error={run.error} onRetry={() => run.refetch()} />
      </>
    );
  }

  const data = run.data;
  const attention = data.steps.find((s) => ["WAITING_HUMAN", "PARKED", "FAILED", "RUNNING"].includes(s.status));
  const current = data.steps.find((s) => s.step_id === selected) ?? attention ?? data.steps[0];

  return (
    <>
      {back}
      <PageHeader
        title={data.subject_label}
        description={`${humanize(data.definition_name)} v${data.definition_version} · started ${dateTime(data.started_at)} · ${data.steps_done}/${data.steps_total} steps · ${inr(data.cost_inr, 4)}`}
        actions={<RunStatusBadge status={data.status} />}
      />
      <div className="space-y-4">
        <LinkedRuns parent={data.parent} kids={data.children} />
        <RunDag steps={data.steps} selected={current?.step_id ?? null} onSelect={setSelected} />
        {current && <StepPanel step={current} />}
        <Card className="p-4">
          <h2 className="text-sm font-medium">Timeline</h2>
          {data.timeline.length === 0 ? (
            <p className="mt-2 text-sm text-muted-foreground">Nothing has happened on this run yet.</p>
          ) : (
            <ol className="mt-3 space-y-2.5">
              {data.timeline.map((event) => (
                <li key={event.seq} className="text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs">{event.action}</span>
                    {event.effect && (
                      <Badge tone={event.effect === "ALLOW" ? "good" : event.effect === "DENY" ? "bad" : "warn"}>
                        {event.effect}
                      </Badge>
                    )}
                    <span className="text-xs text-muted-foreground">
                      {event.actor_id} · {dateTime(event.ts)}
                    </span>
                  </div>
                  {event.reasoning && <p className="mt-0.5 text-xs text-muted-foreground">{event.reasoning}</p>}
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>
    </>
  );
}
