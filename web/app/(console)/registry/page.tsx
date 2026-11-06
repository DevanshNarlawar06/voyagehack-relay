"use client";

import { useQuery } from "@tanstack/react-query";

import { Badge, Card, EmptyState, ErrorState, LoadingState, PageHeader, type Tone } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, humanize } from "@/lib/format";
import type { DefinitionStatus } from "@/lib/types";

const TONES: Record<DefinitionStatus, Tone> = { active: "good", draft: "warn", retired: "neutral" };

export default function RegistryPage() {
  const definitions = useQuery({ queryKey: ["definitions"], queryFn: () => api.definitions() });
  return (
    <>
      <PageHeader
        title="Registry"
        description="Workflow versions. Stored versions never change, so every audit event keeps pointing at what actually ran."
      />
      {definitions.isPending && <LoadingState label="Loading workflows" />}
      {definitions.isError && <ErrorState error={definitions.error} onRetry={() => definitions.refetch()} />}
      {definitions.data?.length === 0 && (
        <EmptyState title="No workflows registered" description="Add a definition under definitions/workflows and register it." />
      )}
      {definitions.data && definitions.data.length > 0 && (
        <Card className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-muted-foreground">
              <tr>
                {["Workflow", "Version", "Status", "Activated by", "Activated", "Content hash"].map((h) => (
                  <th key={h} scope="col" className="px-4 py-2.5 font-normal whitespace-nowrap">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {definitions.data.map((row) => (
                <tr key={`${row.name}@${row.version}`}>
                  <th scope="row" className="px-4 py-2.5 text-left font-medium whitespace-nowrap">
                    {humanize(row.name)}
                  </th>
                  <td className="px-4 py-2.5 tabular-nums">v{row.version}</td>
                  <td className="px-4 py-2.5">
                    <Badge tone={TONES[row.status]}>{humanize(row.status)}</Badge>
                  </td>
                  <td className="px-4 py-2.5 whitespace-nowrap">{row.activated_by ?? "—"}</td>
                  <td className="px-4 py-2.5 whitespace-nowrap">{dateTime(row.activated_at)}</td>
                  <td className="px-4 py-2.5 font-mono text-xs">{row.content_hash.slice(0, 12)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
