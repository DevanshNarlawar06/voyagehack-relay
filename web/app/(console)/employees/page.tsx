"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { RunStatusBadge } from "@/components/status";
import { Badge, Button, Card, EmptyState, ErrorState, LoadingState, PageHeader, type Tone } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, humanize } from "@/lib/format";
import type { EmployeeState } from "@/lib/types";

const STATE_TONES: Record<EmployeeState, Tone> = {
  pre_hire: "neutral",
  onboarding: "accent",
  active: "good",
  notice: "warn",
  exited: "neutral",
};

function Section({ title, empty, children }: { title: string; empty: boolean; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="text-sm font-medium">{title}</h3>
      {empty ? <p className="mt-1 text-sm text-muted-foreground">None recorded.</p> : <div className="mt-2">{children}</div>}
    </section>
  );
}

function EmployeeDetail({ id }: { id: string }) {
  const detail = useQuery({ queryKey: ["employee", id], queryFn: () => api.employee(id) });
  if (detail.isPending) return <LoadingState label="Loading record" rows={4} />;
  if (detail.isError) return <ErrorState error={detail.error} onRetry={() => detail.refetch()} />;
  const record = detail.data;
  const runs = record.runs ?? [];
  return (
    <div className="space-y-5">
      <Section title="Workflows" empty={runs.length === 0}>
        <ul className="space-y-1 text-sm">
          {runs.map((run) => (
            <li key={run.id} className="flex flex-wrap items-center justify-between gap-2">
              <Link href={`/runs/${run.id}`} className="hover:underline">
                {humanize(run.definition_name)}
              </Link>
              <RunStatusBadge status={run.status} />
            </li>
          ))}
        </ul>
      </Section>
      <Section title="Lifecycle" empty={record.lifecycle.length === 0}>
        <ol className="space-y-1 text-sm">
          {record.lifecycle.map((event) => (
            <li key={event.id} className="flex flex-wrap justify-between gap-2">
              <span>{humanize(event.event_type)}</span>
              <span className="text-xs text-muted-foreground">{dateTime(event.at)}</span>
            </li>
          ))}
        </ol>
      </Section>
      <Section title="Documents" empty={record.documents.length === 0}>
        <p className="mb-1 text-xs text-muted-foreground">Contents are not shown here. Sensitive fields stay encrypted.</p>
        <ul className="space-y-1 text-sm">
          {record.documents.map((document) => (
            <li key={document.id} className="flex flex-wrap justify-between gap-2">
              <span>{humanize(document.category)}</span>
              <span className="text-xs text-muted-foreground">
                {document.retention_until ? `Kept until ${document.retention_until}` : "No retention date"}
              </span>
            </li>
          ))}
        </ul>
      </Section>
      <Section title="Consents" empty={record.consents.length === 0}>
        <ul className="space-y-1 text-sm">
          {record.consents.map((consent) => (
            <li key={consent.purpose_id} className="flex flex-wrap items-center justify-between gap-2">
              <span>{humanize(consent.purpose_id)}</span>
              <Badge tone={consent.withdrawn_at ? "warn" : "good"}>
                {consent.withdrawn_at ? "Withdrawn" : `Granted, notice ${consent.notice_version}`}
              </Badge>
            </li>
          ))}
        </ul>
      </Section>
      <Section title="Data requests" empty={record.data_requests.length === 0}>
        <ul className="space-y-1 text-sm">
          {record.data_requests.map((request) => (
            <li key={request.id} className="flex flex-wrap justify-between gap-2">
              <span>{humanize(request.type)}</span>
              <span className="text-muted-foreground">{humanize(request.status)}</span>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}

export default function EmployeesPage() {
  const [open, setOpen] = useState<string | null>(null);
  const employees = useQuery({ queryKey: ["employees"], queryFn: () => api.employees() });
  return (
    <>
      <PageHeader title="Employees" description="The system of record: who is where in their lifecycle, and what they have agreed to." />
      {employees.isPending && <LoadingState label="Loading employees" />}
      {employees.isError && <ErrorState error={employees.error} onRetry={() => employees.refetch()} />}
      {employees.data?.length === 0 && <EmptyState title="No employees yet" description="Records appear once onboarding starts or the seed data is loaded." />}
      {employees.data && employees.data.length > 0 && (
        <Card className="divide-y divide-border">
          {employees.data.map((employee) => (
            <div key={employee.id}>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 text-sm">
                <span className="font-medium">{employee.full_name}</span>
                <Badge tone={STATE_TONES[employee.state]}>{humanize(employee.state)}</Badge>
                {employee.last_working_day && (
                  <span className="text-xs text-muted-foreground">
                    {employee.state === "exited" ? "Left" : "Last day"} {employee.last_working_day}
                  </span>
                )}
                <span className="text-muted-foreground">
                  {[employee.employee_code, employee.role_title, employee.department].filter(Boolean).join(" · ")}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  className="ml-auto"
                  aria-expanded={open === employee.id}
                  aria-label={`${open === employee.id ? "Hide" : "Show"} record for ${employee.full_name}`}
                  onClick={() => setOpen(open === employee.id ? null : employee.id)}
                >
                  {open === employee.id ? "Hide" : "Record"}
                </Button>
              </div>
              {open === employee.id && (
                <div className="border-t border-border bg-background/50 p-4">
                  <EmployeeDetail id={employee.id} />
                </div>
              )}
            </div>
          ))}
        </Card>
      )}
    </>
  );
}
