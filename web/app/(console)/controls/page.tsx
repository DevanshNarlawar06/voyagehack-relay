"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Button, Card, EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateTime, humanize } from "@/lib/format";
import type { FaultMode, KillScope } from "@/lib/types";

const SCOPES: KillScope[] = ["workflow", "step", "skill", "connector"];
const FAULTS: FaultMode[] = ["503", "timeout", "timeout_after_commit", "slow", "duplicate_on_retry"];

function KillSwitches({ canEdit }: { canEdit: boolean }) {
  const client = useQueryClient();
  const switches = useQuery({ queryKey: ["kills"], queryFn: () => api.killSwitches(), refetchInterval: 3_000 });
  const [scope, setScope] = useState<KillScope>("workflow");
  const [target, setTarget] = useState("onboarding");
  const toggle = useMutation({
    mutationFn: (args: { scope: KillScope; target: string; active: boolean }) =>
      api.setKillSwitch(args.scope, args.target, args.active),
    onSuccess: () => client.invalidateQueries({ queryKey: ["kills"] }),
  });
  const active = switches.data?.filter((s) => s.active) ?? [];
  const globalOn = active.some((s) => s.scope === "global");

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium">Kill switches</h2>
          <p className="mt-1 max-w-lg text-sm text-muted-foreground">
            Steps pause at their next boundary, within a second. A call already under way finishes first. Releasing resumes them.
          </p>
        </div>
        <Button
          variant={globalOn ? "secondary" : "danger"}
          disabled={!canEdit || toggle.isPending}
          onClick={() => toggle.mutate({ scope: "global", target: "*", active: !globalOn })}
        >
          {globalOn ? "Release all runs" : "Pause all runs"}
        </Button>
      </div>

      {switches.isPending && <div className="mt-3"><LoadingState label="Loading kill switches" rows={1} /></div>}
      {switches.isError && <div className="mt-3"><ErrorState error={switches.error} onRetry={() => switches.refetch()} /></div>}
      {switches.data && (
        <>
          <form
            className="mt-4 flex flex-wrap items-end gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              if (target.trim()) toggle.mutate({ scope, target: target.trim(), active: true });
            }}
          >
            <label className="text-sm">
              Scope
              <select
                value={scope}
                onChange={(event) => setScope(event.target.value as KillScope)}
                className="mt-1 block h-9 rounded-md border border-border bg-background px-2 text-sm"
              >
                {SCOPES.map((option) => (
                  <option key={option} value={option}>
                    {humanize(option)}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              Target
              <input
                value={target}
                onChange={(event) => setTarget(event.target.value)}
                className="mt-1 block h-9 rounded-md border border-border bg-background px-2 text-sm"
              />
            </label>
            <Button type="submit" disabled={!canEdit || toggle.isPending || !target.trim()}>
              Pause
            </Button>
          </form>
          {active.length === 0 ? (
            <p className="mt-3 text-sm text-muted-foreground">No kill switches are on.</p>
          ) : (
            <ul className="mt-3 divide-y divide-border">
              {active.map((entry) => (
                <li key={`${entry.scope}:${entry.target}`} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                  <span className="flex flex-wrap items-center gap-2">
                    <Badge tone="bad">Paused</Badge>
                    <span className="font-medium">{humanize(entry.scope)}: {entry.target}</span>
                    <span className="text-xs text-muted-foreground">{entry.set_by} · {dateTime(entry.set_at)}</span>
                  </span>
                  <Button
                    size="sm"
                    disabled={!canEdit || toggle.isPending}
                    aria-label={`Release ${entry.scope} ${entry.target}`}
                    onClick={() => toggle.mutate({ scope: entry.scope, target: entry.target, active: false })}
                  >
                    Release
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {toggle.isError && <p role="alert" className="mt-2 text-sm text-danger">{errorMessage(toggle.error)}</p>}
      {!canEdit && <p className="mt-2 text-sm text-muted-foreground">Only HR admins can use kill switches.</p>}
    </Card>
  );
}

function Connectors({ canEdit }: { canEdit: boolean }) {
  const client = useQueryClient();
  const health = useQuery({ queryKey: ["health"], queryFn: () => api.connectorHealth(), refetchInterval: 5_000 });
  const faults = useQuery({ queryKey: ["faults"], queryFn: () => api.faults(), refetchInterval: 5_000 });
  const [fault, setFault] = useState<FaultMode>("503");
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["faults"] });
    void client.invalidateQueries({ queryKey: ["health"] });
  };
  const inject = useMutation({
    mutationFn: (system: string) => api.addFault(system, fault),
    onSuccess: refresh,
  });
  const clear = useMutation({ mutationFn: () => api.clearFaults(), onSuccess: refresh });

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium">Connector health and fault injection</h2>
          <p className="mt-1 text-sm text-muted-foreground">Demo mode only. Faults apply to the mock systems, to show outage handling live.</p>
        </div>
        <div className="flex items-end gap-2">
          <label className="text-sm">
            Fault
            <select
              value={fault}
              onChange={(event) => setFault(event.target.value as FaultMode)}
              className="mt-1 block h-9 rounded-md border border-border bg-background px-2 text-sm"
            >
              {FAULTS.map((option) => (
                <option key={option} value={option}>
                  {option === "503" ? "503 outage" : humanize(option)}
                </option>
              ))}
            </select>
          </label>
          <Button disabled={!canEdit || clear.isPending || !faults.data?.length} onClick={() => clear.mutate()}>
            Clear faults ({faults.data?.length ?? 0})
          </Button>
        </div>
      </div>
      <div className="mt-4">
        {health.isPending && <LoadingState label="Checking connectors" rows={3} />}
        {health.isError && <ErrorState error={health.error} onRetry={() => health.refetch()} />}
        {health.data?.length === 0 && <EmptyState title="No connectors" description="Add manifests under definitions/connectors." />}
        {health.data && health.data.length > 0 && (
          <ul className="divide-y divide-border">
            {health.data.map((entry) => {
              const injected = faults.data?.filter((f) => f.system === entry.connector) ?? [];
              return (
                <li key={entry.connector} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{humanize(entry.connector)}</span>
                    <Badge tone={entry.healthy ? "good" : "bad"}>{entry.healthy ? "Healthy" : "Down"}</Badge>
                    {entry.latency_ms !== null && (
                      <span className="text-xs text-muted-foreground tabular-nums">{Math.round(entry.latency_ms)} ms</span>
                    )}
                    {injected.map((f) => (
                      <Badge key={f.id} tone="warn">Fault: {f.fault}</Badge>
                    ))}
                  </span>
                  <Button
                    size="sm"
                    disabled={!canEdit || inject.isPending}
                    aria-label={`Inject fault into ${entry.connector}`}
                    onClick={() => inject.mutate(entry.connector)}
                  >
                    Inject
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      {(inject.isError || clear.isError) && (
        <p role="alert" className="mt-2 text-sm text-danger">{errorMessage(inject.error ?? clear.error)}</p>
      )}
    </Card>
  );
}

export default function ControlsPage() {
  const { hasRole } = useAuth();
  const canEdit = hasRole("hr_admin");
  return (
    <>
      <PageHeader title="Controls" description="Stop work safely, and see which outside systems are answering." />
      <div className="space-y-4">
        <KillSwitches canEdit={canEdit} />
        <Connectors canEdit={canEdit} />
      </div>
    </>
  );
}
