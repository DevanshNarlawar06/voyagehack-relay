"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";

import { Badge, Button, Card, EmptyState, ErrorState, LoadingState, PageHeader, type Tone } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, humanize } from "@/lib/format";
import type { DataRequestType } from "@/lib/types";

const TYPES: { type: DataRequestType; label: string; detail: string }[] = [
  { type: "access", label: "See my data", detail: "A copy of what Relay holds about you and why." },
  { type: "correction", label: "Correct my data", detail: "HR Ops reviews the change." },
  { type: "erasure", label: "Erase my data", detail: "HR Admin decides, keeping only what the law requires." },
];

const STATUS_TONES: Record<string, Tone> = {
  open: "neutral",
  in_progress: "accent",
  completed: "good",
  rejected: "warn",
};

export default function DataRequestsPage() {
  const client = useQueryClient();
  const requests = useQuery({ queryKey: ["data-requests"], queryFn: () => api.dataRequests() });
  const raise = useMutation({
    mutationFn: (type: DataRequestType) => api.raiseDataRequest(type),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["data-requests"] }),
  });

  return (
    <>
      <PageHeader
        title="Data requests"
        description="DPDP rights: see, correct or erase your own data. Each request runs as a workflow, and erasure is decided by a named person."
      />
      <Card className="mb-4 p-4">
        <h2 className="text-sm font-medium">Raise a request about your own data</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          {TYPES.map(({ type, label, detail }) => (
            <div key={type} className="rounded-md border border-border p-3">
              <Button size="sm" variant="secondary" disabled={raise.isPending} onClick={() => raise.mutate(type)}>
                {label}
              </Button>
              <p className="mt-2 text-xs text-muted-foreground">{detail}</p>
            </div>
          ))}
        </div>
        {raise.isSuccess && (
          <p role="status" className="mt-3 text-sm">
            Request raised. You will get an email when it is answered.
          </p>
        )}
        {raise.isError && (
          <p role="alert" className="mt-3 text-sm text-danger">
            {raise.error instanceof Error ? raise.error.message : "The request could not be raised."}
          </p>
        )}
      </Card>

      {requests.isPending && <LoadingState label="Loading data requests" />}
      {requests.isError && <ErrorState error={requests.error} onRetry={() => requests.refetch()} />}
      {requests.data?.length === 0 && <EmptyState title="No data requests" description="Requests appear here once someone raises one." />}
      {requests.data && requests.data.length > 0 && (
        <Card className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-2 font-normal">Person</th>
                <th className="px-4 py-2 font-normal">Request</th>
                <th className="px-4 py-2 font-normal">Status</th>
                <th className="px-4 py-2 font-normal">Raised</th>
                <th className="px-4 py-2 font-normal">Run</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {requests.data.map((row) => (
                <tr key={row.id}>
                  <th scope="row" className="px-4 py-2 text-left font-medium">
                    {row.person_name}
                  </th>
                  <td className="px-4 py-2">{humanize(row.type)}</td>
                  <td className="px-4 py-2">
                    <Badge tone={STATUS_TONES[row.status] ?? "neutral"}>{humanize(row.status)}</Badge>
                  </td>
                  <td className="px-4 py-2 text-muted-foreground">{dateTime(row.created_at)}</td>
                  <td className="px-4 py-2">
                    {row.run_id ? (
                      <Link href={`/runs/${row.run_id}`} className="underline">
                        View run
                      </Link>
                    ) : (
                      <span className="text-muted-foreground">Not started</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
