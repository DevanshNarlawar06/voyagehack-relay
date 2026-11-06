// Status is always shown as text as well as colour, so it survives colour blindness and greyscale.
import { Badge, type Tone } from "@/components/ui";
import { humanize } from "@/lib/format";
import type { RunStatus, StepStatus, TaskKind } from "@/lib/types";

const RUN_TONES: Record<RunStatus, Tone> = {
  running: "accent",
  waiting: "warn",
  paused: "warn",
  completed: "good",
  failed: "bad",
  cancelled: "neutral",
};

export function RunStatusBadge({ status }: { status: RunStatus }) {
  return <Badge tone={RUN_TONES[status]}>{humanize(status)}</Badge>;
}

export const STEP_TONES: Record<StepStatus, Tone> = {
  PENDING: "neutral",
  READY: "neutral",
  RUNNING: "accent",
  SUCCEEDED: "good",
  WAITING_HUMAN: "warn",
  WAITING_TIMER: "neutral",
  RETRY_SCHEDULED: "warn",
  PARKED: "bad",
  FAILED: "bad",
  PAUSED: "warn",
  SKIPPED: "neutral",
};

export function stepLabel(status: StepStatus): string {
  return humanize(status.toLowerCase());
}

export function StepStatusBadge({ status }: { status: StepStatus }) {
  return <Badge tone={STEP_TONES[status]}>{stepLabel(status)}</Badge>;
}

export const TASK_KIND_LABELS: Record<TaskKind, string> = {
  reserved_decision: "Reserved decisions",
  approval: "Approvals",
  conflict: "Conflicts",
  exception: "Exceptions",
  low_confidence: "Low confidence",
  outage: "Outages",
  route_only: "Route only",
};
