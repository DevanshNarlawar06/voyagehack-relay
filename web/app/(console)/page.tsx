"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Bar, BarChart, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Card, EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import { duration, humanize, inr, percent } from "@/lib/format";
import type { MetricsSummary, RunStatus, WorkflowMetrics } from "@/lib/types";

const STATUS_ORDER: RunStatus[] = ["running", "waiting", "paused", "completed", "failed", "cancelled"];

function Stat({
  label,
  value,
  note,
  href,
}: {
  label: string;
  value: string;
  note: string;
  href?: string;
}) {
  const body = (
    <>
      <p className="text-[11.5px] font-semibold tracking-[0.06em] text-muted-foreground uppercase">{label}</p>
      <p className="mt-2 text-[30px] leading-none font-semibold tracking-tight tabular-nums">{value}</p>
      <p className="mt-2 text-xs text-muted-foreground">{note}</p>
    </>
  );
  return (
    <Card className="p-4 hover:border-muted-foreground/40">
      {href ? (
        <Link href={href} className="block">
          {body}
        </Link>
      ) : (
        body
      )}
    </Card>
  );
}

function RunsByStatus({ metrics }: { metrics: MetricsSummary }) {
  const rows = STATUS_ORDER.map((status) => ({
    status: humanize(status),
    runs: metrics.runs_by_status[status] ?? 0,
  }));
  return (
    <Card className="p-4">
      <h2 className="text-sm font-medium">Runs by status</h2>
      <div className="mt-3 h-56">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} layout="vertical" margin={{ left: 8, right: 32, top: 0, bottom: 0 }}>
            <XAxis type="number" hide allowDecimals={false} />
            <YAxis
              type="category"
              dataKey="status"
              width={80}
              axisLine={false}
              tickLine={false}
              tick={{ fill: "var(--muted-foreground)", fontSize: 12 }}
            />
            <Tooltip
              cursor={{ fill: "var(--muted)" }}
              contentStyle={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: 6,
                fontSize: 12,
                color: "var(--foreground)",
              }}
            />
            <Bar dataKey="runs" name="Runs" fill="var(--accent)" radius={[0, 4, 4, 0]} barSize={14} isAnimationActive={false}>
              <LabelList dataKey="runs" position="right" style={{ fill: "var(--foreground)", fontSize: 12 }} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}

function CostCard({ metrics }: { metrics: MetricsSummary }) {
  const rows = [
    ["Variable cost, all completed runs", inr(metrics.variable_cost_inr)],
    ["Infrastructure allocated", inr(metrics.infra_alloc_inr)],
    ["Variable cost per run", inr(metrics.variable_cost_per_workflow_inr, 4)],
    ["Monthly projection", inr(metrics.monthly_projection_inr)],
  ];
  return (
    <Card className="p-4">
      <h2 className="text-sm font-medium">Cost in INR</h2>
      <dl className="mt-3 divide-y divide-border text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-4 py-2">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="font-medium tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-2 text-xs text-muted-foreground">
        {metrics.monthly_projection_inr === null
          ? "Projection appears once TBO's monthly volumes are in config/volumes.yaml."
          : metrics.projection_note}
      </p>
    </Card>
  );
}

function WorkflowTable({ workflows }: { workflows: Record<string, WorkflowMetrics> }) {
  const names = Object.keys(workflows);
  if (names.length === 0) return null;
  return (
    <Card className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="px-4 pt-4 text-left font-medium">By workflow</caption>
        <thead className="text-left text-muted-foreground">
          <tr>
            {["Workflow", "Completed", "Automation", "Straight through", "Escalation", "Cost per run"].map((h) => (
              <th key={h} scope="col" className="px-4 py-2 font-normal">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border tabular-nums">
          {names.map((name) => {
            const row = workflows[name];
            return (
              <tr key={name}>
                <th scope="row" className="px-4 py-2 text-left font-medium">
                  {humanize(name)}
                </th>
                <td className="px-4 py-2">{row.completed_runs}</td>
                <td className="px-4 py-2">{percent(row.step_automation_rate)}</td>
                <td className="px-4 py-2">{percent(row.straight_through_rate)}</td>
                <td className="px-4 py-2">{percent(row.human_escalation_rate)}</td>
                <td className="px-4 py-2">{inr(row.cost_per_workflow_inr)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}

export default function DashboardPage() {
  const metrics = useQuery({
    queryKey: ["metrics"],
    queryFn: () => api.metrics(),
    refetchInterval: 5_000,
  });

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Automation, speed and cost, measured from completed runs."
      />
      {metrics.isPending && <LoadingState label="Loading metrics" rows={5} />}
      {metrics.isError && <ErrorState error={metrics.error} onRetry={() => metrics.refetch()} />}
      {metrics.data?.completed_runs === 0 && (
        <EmptyState
          title="No completed runs yet"
          description="Metrics appear once a workflow finishes. Start one by sending a candidate.offer_accepted event."
        />
      )}
      {metrics.data && metrics.data.completed_runs > 0 && (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <Stat
              label="Step automation"
              value={percent(metrics.data.step_automation_rate)}
              note={`${metrics.data.steps_completed} steps completed`}
              href="/runs?status=completed"
            />
            <Stat
              label="Straight through"
              value={percent(metrics.data.straight_through_rate)}
              note="No touch but reserved decisions"
              href="/runs?status=completed"
            />
            <Stat
              label="Human escalation"
              value={percent(metrics.data.human_escalation_rate)}
              note="Runs with a non-reserved task"
              href="/inbox"
            />
            <Stat
              label="Cycle time p50"
              value={duration(metrics.data.cycle_time_p50_seconds)}
              note={`p90 ${duration(metrics.data.cycle_time_p90_seconds)}, excludes joiner waits`}
              href="/runs?status=completed"
            />
            <Stat
              label="Cost per workflow"
              value={inr(metrics.data.cost_per_workflow_inr)}
              note={`Over ${metrics.data.completed_runs} completed runs`}
            />
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <RunsByStatus metrics={metrics.data} />
            <CostCard metrics={metrics.data} />
          </div>
          <WorkflowTable workflows={metrics.data.by_workflow} />
        </div>
      )}
    </>
  );
}
