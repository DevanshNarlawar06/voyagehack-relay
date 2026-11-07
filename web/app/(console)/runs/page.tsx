"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { RunStatusBadge } from "@/components/status";
import { Button, Card, EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, humanize, inr } from "@/lib/format";
import type { RunStatus } from "@/lib/types";

const FILTERS: RunStatus[] = ["running", "waiting", "paused", "completed", "failed", "cancelled"];
const WORKFLOWS = ["onboarding", "onboarding_probation", "exit"];

function href(status?: string, workflow?: string): string {
  const query = new URLSearchParams();
  if (status) query.set("status", status);
  if (workflow) query.set("workflow", workflow);
  const qs = query.toString();
  return qs ? `/runs?${qs}` : "/runs";
}

function RunsContent() {
  const params = useSearchParams();
  const router = useRouter();
  const raw = params.get("status");
  const status = FILTERS.find((f) => f === raw);
  const workflow = WORKFLOWS.find((w) => w === params.get("workflow"));
  const runs = useQuery({
    queryKey: ["runs", status, workflow],
    queryFn: () => api.runs(status, workflow),
    refetchInterval: 3_000,
  });

  return (
    <>
      <PageHeader title="Runs" description="Every workflow run, newest first." />
      <div className="mb-4 flex flex-wrap gap-1.5" role="group" aria-label="Filter by status">
        <Button size="sm" variant={status ? "ghost" : "secondary"} aria-pressed={!status} onClick={() => router.push(href(undefined, workflow))}>
          All
        </Button>
        {FILTERS.map((filter) => (
          <Button
            key={filter}
            size="sm"
            variant={status === filter ? "secondary" : "ghost"}
            aria-pressed={status === filter}
            onClick={() => router.push(href(filter, workflow))}
          >
            {humanize(filter)}
          </Button>
        ))}
      </div>
      <div className="mb-4 flex flex-wrap gap-1.5" role="group" aria-label="Filter by workflow">
        <Button size="sm" variant={workflow ? "ghost" : "secondary"} aria-pressed={!workflow} onClick={() => router.push(href(status))}>
          All workflows
        </Button>
        {WORKFLOWS.map((name) => (
          <Button
            key={name}
            size="sm"
            variant={workflow === name ? "secondary" : "ghost"}
            aria-pressed={workflow === name}
            onClick={() => router.push(href(status, name))}
          >
            {humanize(name)}
          </Button>
        ))}
      </div>
      {runs.isPending && <LoadingState label="Loading runs" />}
      {runs.isError && <ErrorState error={runs.error} onRetry={() => runs.refetch()} />}
      {runs.data?.length === 0 && (
        <EmptyState
          title={status || workflow ? "No runs match these filters" : "No runs yet"}
          description={status || workflow ? "Try another filter." : "A run starts when a candidate accepts an approved offer."}
        />
      )}
      {runs.data && runs.data.length > 0 && (
        <Card className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-muted-foreground">
              <tr>
                {["Subject", "Workflow", "Status", "Steps", "Started", "Cost"].map((h) => (
                  <th key={h} scope="col" className="px-4 py-2.5 font-normal whitespace-nowrap">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {runs.data.map((run) => (
                <tr key={run.id} className="hover:bg-muted/50">
                  <th scope="row" className="px-4 py-2.5 text-left font-medium whitespace-nowrap">
                    <Link href={`/runs/${run.id}`} className="hover:underline">
                      {run.subject_label}
                    </Link>
                  </th>
                  <td className="px-4 py-2.5 whitespace-nowrap">
                    {humanize(run.definition_name)} v{run.definition_version}
                    {run.parent_run_id && (
                      <Link href={`/runs/${run.parent_run_id}`} className="ml-2 text-xs text-muted-foreground hover:underline">
                        child run
                      </Link>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    <RunStatusBadge status={run.status} />
                  </td>
                  <td className="px-4 py-2.5 tabular-nums">
                    {run.steps_done}/{run.steps_total}
                  </td>
                  <td className="px-4 py-2.5 whitespace-nowrap">{dateTime(run.started_at)}</td>
                  <td className="px-4 py-2.5 tabular-nums">{inr(run.cost_inr, 4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}

export default function RunsPage() {
  return (
    <Suspense fallback={<LoadingState label="Loading runs" />}>
      <RunsContent />
    </Suspense>
  );
}
