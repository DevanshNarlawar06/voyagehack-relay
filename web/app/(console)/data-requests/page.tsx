"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Badge, Button, Card, EmptyState, ErrorState, LoadingState, PageHeader, type Tone } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateTime, humanize } from "@/lib/format";
import type { DataRequestType } from "@/lib/types";

const TYPES: { type: DataRequestType; label: string; detail: string }[] = [
  { type: "access", label: "See my data", detail: "A copy of what Relay holds about you and why." },
  { type: "correction", label: "Correct my data", detail: "HR Ops reviews the change." },
  { type: "erasure", label: "Erase my data", detail: "HR Admin decides, keeping only what the law requires." },
];

// These decide what to offer, from rbac.yaml. The API still decides what is allowed.
const CAN_RAISE = ["hr_admin", "hr_ops", "manager", "employee"];
const CAN_OPEN_RUNS = ["hr_admin", "hr_ops", "auditor", "developer"];

const STATUS_TONES: Record<string, Tone> = {
  open: "neutral",
  in_progress: "accent",
  completed: "good",
  rejected: "warn",
};

export default function DataRequestsPage() {
  const client = useQueryClient();
  const { hasRole } = useAuth();
  const canRaise = hasRole(...CAN_RAISE);
  const canOpenRuns = hasRole(...CAN_OPEN_RUNS);
  const [confirmingErasure, setConfirmingErasure] = useState(false);
  const requests = useQuery({ queryKey: ["data-requests"], queryFn: () => api.dataRequests() });
  const [details, setDetails] = useState("");
  const raise = useMutation({
    mutationFn: (type: DataRequestType) => api.raiseDataRequest(type, details.trim()),
    onSuccess: () => {
      setDetails("");
      void client.invalidateQueries({ queryKey: ["data-requests"] });
    },
  });

  return (
    <>
      <PageHeader
        title="Data requests"
        description={
          canRaise
            ? "DPDP rights: see, correct or erase your own data. Each request runs as a workflow, and erasure is decided by a named person."
            : "DPDP requests people have raised. Each runs as a workflow, and erasure is decided by a named person."
        }
      />
      {canRaise && (
      <Card className="mb-4 p-4">
        <h2 className="text-sm font-medium">Raise a request about your own data</h2>
        <label htmlFor="request-details" className="mt-3 block text-sm">
          What should change, or why (needed for a correction)
        </label>
        <textarea
          id="request-details"
          value={details}
          maxLength={1000}
          rows={2}
          onChange={(event) => setDetails(event.target.value)}
          placeholder="For example: my surname is spelt Singh, not Sing"
          className="mt-1 w-full rounded-md border border-border bg-background p-2 text-sm"
        />
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          {TYPES.map(({ type, label, detail }) => (
            <div key={type} className="rounded-md border border-border p-3">
              <Button
                size="sm"
                variant="secondary"
                disabled={raise.isPending || confirmingErasure || (type === "correction" && !details.trim())}
                onClick={() => (type === "erasure" ? setConfirmingErasure(true) : raise.mutate(type))}
              >
                {label}
              </Button>
              <p className="mt-2 text-xs text-muted-foreground">{detail}</p>
            </div>
          ))}
        </div>
        {confirmingErasure && (
          <div role="group" aria-label="Confirm erasure request" className="mt-3 rounded-md border border-border p-3">
            <p className="text-sm">
              Send HR Admin a request to erase your data? They decide, and anything the law requires Relay to keep stays.
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="primary"
                onClick={() => {
                  setConfirmingErasure(false);
                  raise.mutate("erasure");
                }}
              >
                Send erasure request
              </Button>
              <Button size="sm" onClick={() => setConfirmingErasure(false)}>
                Cancel
              </Button>
            </div>
          </div>
        )}
        {raise.isSuccess && (
          <p role="status" className="mt-3 text-sm">
            Request raised. You will get an email when it is answered.
          </p>
        )}
        {raise.isError && (
          <p role="alert" className="mt-3 text-sm text-danger">
            {errorMessage(raise.error)}
          </p>
        )}
      </Card>
      )}

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
                {canOpenRuns && <th className="px-4 py-2 font-normal">Run</th>}
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
                  {canOpenRuns && (
                    <td className="px-4 py-2">
                      {row.run_id ? (
                        <Link href={`/runs/${row.run_id}`} className="underline">
                          View run
                        </Link>
                      ) : (
                        <span className="text-muted-foreground">Not started</span>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
