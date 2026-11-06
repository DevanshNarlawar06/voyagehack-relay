// The console's contract with the Relay API (PRD 16.1). Metric names match relay/core/cost/metrics.py.

export type RunStatus = "running" | "paused" | "waiting" | "completed" | "failed" | "cancelled";

export type StepKind = "rule" | "connector" | "skill" | "human" | "wait" | "parallel";

export type StepStatus =
  | "PENDING"
  | "READY"
  | "RUNNING"
  | "SUCCEEDED"
  | "WAITING_HUMAN"
  | "WAITING_TIMER"
  | "RETRY_SCHEDULED"
  | "PARKED"
  | "FAILED"
  | "PAUSED"
  | "SKIPPED";

export type TaskKind =
  | "reserved_decision"
  | "approval"
  | "conflict"
  | "exception"
  | "low_confidence"
  | "outage"
  | "route_only";

export type TaskStatus = "open" | "claimed" | "decided" | "expired" | "cancelled";

export interface User {
  email: string;
  display_name: string;
  roles: string[];
}

export interface Session {
  token: string;
  user: User;
}

export interface WorkflowMetrics {
  completed_runs: number;
  steps_completed: number;
  subject_actions: number;
  step_automation_rate: number | null;
  straight_through_rate: number | null;
  human_escalation_rate: number | null;
  cycle_time_p50_seconds: number | null;
  cycle_time_p90_seconds: number | null;
  subject_wait_p50_seconds: number | null;
  variable_cost_inr: string;
  infra_alloc_inr: string;
  cost_per_workflow_inr: string | null;
  variable_cost_per_workflow_inr: string | null;
}

export interface MetricsSummary extends WorkflowMetrics {
  runs_by_status: Partial<Record<RunStatus, number>>;
  by_workflow: Record<string, WorkflowMetrics>;
  monthly_projection_inr: string | null;
  projection_note: string;
}

export interface RunSummary {
  id: string;
  definition_name: string;
  definition_version: number;
  subject_label: string;
  status: RunStatus;
  started_at: string;
  completed_at: string | null;
  steps_total: number;
  steps_done: number;
  cost_inr: string;
}

export interface Step {
  step_id: string;
  kind: StepKind;
  status: StepStatus;
  needs: string[];
  attempt: number;
  human_touched: boolean;
  started_at: string | null;
  finished_at: string | null;
  cost_inr: string;
  action: string | null;
  decision: {
    effect: "ALLOW" | "REQUIRE_HUMAN" | "DENY";
    rule_ids: string[];
    policy_version: string;
    reason: string;
  } | null;
  model_id: string | null;
  prompt_version: string | null;
  error: string | null;
}

export interface TimelineEvent {
  seq: number;
  ts: string;
  actor_type: "human" | "agent" | "system";
  actor_id: string;
  action: string;
  effect: string | null;
  step_id: string | null;
  reasoning: string | null;
}

export interface RunDetail extends RunSummary {
  steps: Step[];
  timeline: TimelineEvent[];
}

export interface HumanTask {
  id: string;
  run_id: string | null;
  step_id: string | null;
  kind: TaskKind;
  action: string;
  title: string;
  assignee_role: string;
  status: TaskStatus;
  options: string[];
  sla_due_at: string | null;
  context_packet: {
    summary: string;
    likely_cause?: string;
    suggested_option?: string;
    facts: Record<string, string>;
    rule?: string;
  };
  decision: string | null;
  reason: string | null;
}

export interface PolicyPackSummary {
  pack: string;
  version: number;
  owner: string;
  activated_by: string | null;
  activated_at: string | null;
}

export interface PolicyPack extends PolicyPackSummary {
  content: string;
  draft: string | null;
  history: { version: number; activated_by: string | null; activated_at: string | null }[];
}

export interface PolicyTestResult {
  name: string;
  passed: boolean;
  detail: string | null;
}

export type KillScope = "global" | "workflow" | "step" | "skill" | "connector";

export interface KillSwitch {
  scope: KillScope;
  target: string;
  active: boolean;
  set_by: string;
  set_at: string;
  note: string | null;
}

export interface ConnectorHealth {
  connector: string;
  healthy: boolean;
  status_code: number | null;
  latency_ms: number | null;
  detail: string | null;
}

export type FaultMode = "503" | "timeout" | "timeout_after_commit" | "slow" | "duplicate_on_retry";

export interface FaultRule {
  id: string;
  system: string;
  op: string;
  fault: FaultMode;
  remaining: number | null;
}

export interface RelayApi {
  /** True when this client serves fixtures, so the UI can say the numbers are not measured. */
  readonly isDemoData: boolean;
  demoUsers(): Promise<User[]>;
  login(email: string): Promise<Session>;
  metrics(workflow?: string): Promise<MetricsSummary>;
  runs(status?: RunStatus): Promise<RunSummary[]>;
  run(id: string): Promise<RunDetail>;
  tasks(): Promise<HumanTask[]>;
  decideTask(id: string, option: string, reason: string): Promise<HumanTask>;
  policies(): Promise<PolicyPackSummary[]>;
  policy(pack: string): Promise<PolicyPack>;
  saveDraft(pack: string, content: string): Promise<void>;
  testPolicy(pack: string): Promise<PolicyTestResult[]>;
  activatePolicy(pack: string): Promise<PolicyPackSummary>;
  killSwitches(): Promise<KillSwitch[]>;
  setKillSwitch(scope: KillScope, target: string, active: boolean, note?: string): Promise<KillSwitch>;
  connectorHealth(): Promise<ConnectorHealth[]>;
  faults(): Promise<FaultRule[]>;
  addFault(system: string, fault: FaultMode): Promise<FaultRule>;
  clearFaults(): Promise<void>;
}
