// Run graph: steps in columns by dependency depth, edges drawn from `needs`.
"use client";

import { useEffect, useMemo, useRef } from "react";

import { STEP_TONES, stepLabel } from "@/components/status";
import type { Tone } from "@/components/ui";
import { cn, humanize } from "@/lib/format";
import type { Step } from "@/lib/types";

const NODE_W = 176;
const NODE_H = 60;
const GAP_X = 44;
const GAP_Y = 12;
const PAD = 8;

const NODE_TONES: Record<Tone, string> = {
  neutral: "border-border bg-surface",
  accent: "border-accent bg-accent/10",
  good: "border-good/50 bg-good/10",
  warn: "border-warn bg-warn/15",
  bad: "border-danger bg-danger/10",
};

export function layoutDag(steps: Step[]) {
  const byId = new Map(steps.map((step) => [step.step_id, step]));
  const depths = new Map<string, number>();
  const depthOf = (id: string, seen: Set<string>): number => {
    const known = depths.get(id);
    if (known !== undefined) return known;
    if (seen.has(id)) return 0; // the registry rejects cycles; never hang the page on one
    seen.add(id);
    const needs = byId.get(id)?.needs.filter((need) => byId.has(need)) ?? [];
    const depth = needs.length ? 1 + Math.max(...needs.map((need) => depthOf(need, seen))) : 0;
    depths.set(id, depth);
    return depth;
  };
  steps.forEach((step) => depthOf(step.step_id, new Set()));

  const columns: Step[][] = [];
  steps.forEach((step) => {
    const depth = depths.get(step.step_id) ?? 0;
    (columns[depth] ??= []).push(step);
  });

  const tallest = Math.max(1, ...columns.map((column) => column.length));
  const positions = new Map<string, { x: number; y: number }>();
  columns.forEach((column, x) => {
    // Centre each column, so a long chain beside a wide fan-out does not leave a hole below it.
    const offset = ((tallest - column.length) * (NODE_H + GAP_Y)) / 2;
    column.forEach((step, y) =>
      positions.set(step.step_id, {
        x: PAD + x * (NODE_W + GAP_X),
        y: PAD + offset + y * (NODE_H + GAP_Y),
      }),
    );
  });
  return {
    positions,
    width: PAD * 2 + columns.length * (NODE_W + GAP_X) - GAP_X,
    height: PAD * 2 + tallest * (NODE_H + GAP_Y) - GAP_Y,
  };
}

export function RunDag({
  steps,
  selected,
  onSelect,
}: {
  steps: Step[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const { positions, width, height } = useMemo(() => layoutDag(steps), [steps]);
  const scroller = useRef<HTMLDivElement>(null);
  const focusX = selected ? positions.get(selected)?.x : undefined;

  // Bring the selected step into view: on a long run it is often the one that needs attention.
  useEffect(() => {
    const element = scroller.current;
    if (!element || focusX === undefined) return;
    if (focusX < element.scrollLeft || focusX + NODE_W > element.scrollLeft + element.clientWidth) {
      element.scrollLeft = Math.max(0, focusX - (element.clientWidth - NODE_W) / 2);
    }
  }, [focusX]);

  return (
    <div ref={scroller} className="overflow-x-auto rounded-lg border border-border bg-background" tabIndex={0} aria-label="Run graph, scrollable">
      <div className="relative" style={{ width, height }}>
        <svg className="absolute inset-0 text-muted-foreground/50" width={width} height={height} aria-hidden>
          {steps.flatMap((step) =>
            step.needs.map((need) => {
              const from = positions.get(need);
              const to = positions.get(step.step_id);
              if (!from || !to) return null;
              const x1 = from.x + NODE_W;
              const y1 = from.y + NODE_H / 2;
              const x2 = to.x;
              const y2 = to.y + NODE_H / 2;
              const mid = (x1 + x2) / 2;
              return (
                <path
                  key={`${need}-${step.step_id}`}
                  d={`M${x1} ${y1} C${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={1.5}
                />
              );
            }),
          )}
        </svg>
        {steps.map((step) => {
          const position = positions.get(step.step_id);
          if (!position) return null;
          return (
            <button
              key={step.step_id}
              type="button"
              onClick={() => onSelect(step.step_id)}
              aria-pressed={selected === step.step_id}
              data-status={step.status}
              className={cn(
                "absolute flex flex-col justify-center rounded-md border px-2.5 text-left",
                NODE_TONES[STEP_TONES[step.status]],
                selected === step.step_id && "ring-2 ring-accent ring-offset-1 ring-offset-background",
              )}
              style={{ left: position.x, top: position.y, width: NODE_W, height: NODE_H }}
            >
              <span className="truncate text-xs font-medium">{humanize(step.step_id)}</span>
              <span className="truncate text-[11px] text-muted-foreground">
                {step.kind} · {stepLabel(step.status)}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
