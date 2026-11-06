"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Badge, Button, Card, EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn, dateTime, humanize, lineDiff } from "@/lib/format";
import type { PolicyPack, PolicyTestResult } from "@/lib/types";

function Editor({ pack }: { pack: PolicyPack }) {
  const client = useQueryClient();
  const { hasRole } = useAuth();
  const [text, setText] = useState(pack.draft ?? pack.content);
  const [results, setResults] = useState<PolicyTestResult[] | null>(null);
  const [activated, setActivated] = useState<number | null>(null);
  const canEdit = hasRole("hr_admin");
  const dirty = text !== pack.content;
  const passed = results !== null && results.every((r) => r.passed);

  useEffect(() => {
    setText(pack.draft ?? pack.content);
    setResults(null);
  }, [pack.pack, pack.version, pack.content, pack.draft]);

  const runTests = useMutation({
    mutationFn: async () => {
      await api.saveDraft(pack.pack, text);
      return api.testPolicy(pack.pack);
    },
    onSuccess: (data) => {
      setResults(data);
      setActivated(null);
    },
  });
  const activate = useMutation({
    mutationFn: () => api.activatePolicy(pack.pack),
    onSuccess: (data) => {
      setActivated(data.version);
      setResults(null);
      void client.invalidateQueries({ queryKey: ["policies"] });
      void client.invalidateQueries({ queryKey: ["policy", pack.pack] });
    },
  });

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-medium">
            {humanize(pack.pack)} <span className="text-muted-foreground">active v{pack.version}</span>
          </h2>
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={!canEdit || !dirty || runTests.isPending}
              onClick={() => runTests.mutate()}
            >
              {runTests.isPending ? "Running tests…" : "Run tests"}
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={!canEdit || !passed || activate.isPending}
              onClick={() => activate.mutate()}
            >
              Activate
            </Button>
          </div>
        </div>
        <label htmlFor="policy-yaml" className="sr-only">
          Policy YAML
        </label>
        <textarea
          id="policy-yaml"
          value={text}
          readOnly={!canEdit}
          spellCheck={false}
          rows={Math.min(18, text.split("\n").length + 1)}
          onChange={(event) => {
            setText(event.target.value);
            setResults(null);
            setActivated(null);
          }}
          className="mt-3 w-full rounded-md border border-border bg-background p-3 font-mono text-xs leading-relaxed"
        />
        {!canEdit && <p className="mt-2 text-sm text-muted-foreground">Only HR admins can edit and activate policies.</p>}
        <p className="mt-2 text-xs text-muted-foreground">
          The baseline reserved list is compiled in, so a pack can add to it but never remove from it. Tests must pass before activation.
        </p>
        {runTests.isError && (
          <p role="alert" className="mt-2 text-sm text-danger">
            {errorMessage(runTests.error)}
          </p>
        )}
        {activate.isError && (
          <p role="alert" className="mt-2 text-sm text-danger">
            {errorMessage(activate.error)}
          </p>
        )}
        {activated !== null && (
          <p role="status" className="mt-2 text-sm text-good">
            Version {activated} is active. It applies to the next policy check, with no restart.
          </p>
        )}
      </Card>

      {results && (
        <Card className="p-4">
          <h3 className="text-sm font-medium">
            Test results: {results.filter((r) => r.passed).length} of {results.length} passed
          </h3>
          <ul className="mt-2 space-y-1.5 text-sm">
            {results.map((result) => (
              <li key={result.name} className="flex flex-wrap items-center gap-2">
                <Badge tone={result.passed ? "good" : "bad"}>{result.passed ? "Pass" : "Fail"}</Badge>
                {result.name}
                {result.detail && <span className="text-muted-foreground">{result.detail}</span>}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {dirty && (
        <Card className="overflow-x-auto p-4">
          <h3 className="text-sm font-medium">Changes from active v{pack.version}</h3>
          <pre className="mt-2 font-mono text-xs leading-relaxed">
            {lineDiff(pack.content, text).map((line, index) => (
              <div
                key={index}
                className={cn(line.kind === "add" && "bg-good/12", line.kind === "del" && "bg-danger/12")}
              >
                {line.kind === "add" ? "+ " : line.kind === "del" ? "- " : "  "}
                {line.text}
              </div>
            ))}
          </pre>
        </Card>
      )}

      <Card className="p-4">
        <h3 className="text-sm font-medium">Version history</h3>
        <ul className="mt-2 divide-y divide-border text-sm">
          {pack.history.map((entry, index) => (
            <li key={`${entry.version}-${index}`} className="flex flex-wrap justify-between gap-2 py-2">
              <span className="font-medium">v{entry.version}</span>
              <span className="text-muted-foreground">
                {entry.activated_by ?? "Not activated"} · {dateTime(entry.activated_at)}
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

export default function PoliciesPage() {
  const packs = useQuery({ queryKey: ["policies"], queryFn: () => api.policies() });
  const [selected, setSelected] = useState<string | null>(null);
  const current = selected ?? packs.data?.[0]?.pack ?? null;
  const pack = useQuery({
    queryKey: ["policy", current],
    queryFn: () => api.policy(current as string),
    enabled: current !== null,
  });

  return (
    <>
      <PageHeader title="Policies" description="Rules live here, versioned and tested, not in prompts." />
      {packs.isPending && <LoadingState label="Loading policy packs" />}
      {packs.isError && <ErrorState error={packs.error} onRetry={() => packs.refetch()} />}
      {packs.data?.length === 0 && <EmptyState title="No policy packs" description="Add packs under definitions/policies." />}
      {packs.data && packs.data.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-[13rem_1fr]">
          <nav aria-label="Policy packs" className="flex gap-1 overflow-x-auto lg:flex-col">
            {packs.data.map((entry) => (
              <Button
                key={entry.pack}
                variant={entry.pack === current ? "secondary" : "ghost"}
                aria-current={entry.pack === current ? "true" : undefined}
                className="justify-between gap-3"
                onClick={() => setSelected(entry.pack)}
              >
                {humanize(entry.pack)}
                <span className="text-xs text-muted-foreground">v{entry.version}</span>
              </Button>
            ))}
          </nav>
          <div className="min-w-0">
            {pack.isPending && <LoadingState label="Loading policy" rows={6} />}
            {pack.isError && <ErrorState error={pack.error} onRetry={() => pack.refetch()} />}
            {pack.data && <Editor key={pack.data.pack} pack={pack.data} />}
          </div>
        </div>
      )}
    </>
  );
}
