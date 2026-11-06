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

function RunsContent() {
  const params = useSearchParams();
  const router = useRouter();
  const raw = params.get("status");
  const status = FILTERS.find((f) => f === raw);
  const runs = useQuery({
    queryKey: ["runs", status],
    queryFn: () => api.runs(status),
    refetchInterval: 3_000,
  });

  return (
    <>
      <PageHeader title="Runs" description="Every workflow run, newest first." />
      <div className="mb-4 flex flex-wrap gap-1.5" role="group" aria-label="Filter by status">
        <Button size="sm" variant={status ? "ghost" : "secondary"} aria-pressed={!status} onClick={() => router.push("/runs")}>
          All
        </Button>
        {FILTERS.map((filter) => (
          <Button
            key={filter}
            size="sm"
            variant={status === filter ? "secondary" : "ghost"}
            aria-pressed={status === filter}
            onClick={() => router.push(`/runs?status=${filter}`)}
          >
            {humanize(filter)}
          </Button>
        ))}
      </div>
      {runs.isPending && <LoadingState label="Loading runs" />}
      {runs.isError && <ErrorState error={runs.error} onRetry={() => runs.refetch()} />}
      {runs.data?.length === 0 && (
        <EmptyState
          title={status ? `No ${status} runs` : "No runs yet"}
          description={status ? "Try another filter." : "A run starts when a candidate accepts an approved offer."}
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
