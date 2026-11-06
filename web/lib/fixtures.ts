// Invented data for building the screens before the API exists. Nothing here is measured, and the
// shell labels it as such. All people and identifiers are synthetic.
import type {
  ConnectorHealth,
  FaultRule,
  HumanTask,
  KillSwitch,
  MetricsSummary,
  PolicyPack,
  RelayApi,
  RunDetail,
  RunSummary,
  Step,
  StepKind,
  StepStatus,
  User,
} from "@/lib/types";

const USERS: User[] = [
  { email: "hr.admin@tbo-demo.test", display_name: "Meera Nair", roles: ["hr_admin", "hr_ops"] },
  { email: "hr.ops@tbo-demo.test", display_name: "Rohan Das", roles: ["hr_ops"] },
  { email: "it.approver@tbo-demo.test", display_name: "Kabir Shah", roles: ["approver_it_spend"] },
  { email: "auditor@tbo-demo.test", display_name: "Leela Iyer", roles: ["auditor"] },
];

const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
const minutesAhead = (minutes: number) => new Date(Date.now() + minutes * 60_000).toISOString();

const ONBOARDING: [string, StepKind, string[], string | null][] = [
  ["verify_offer_approval", "rule", [], "hiring.offer.verify"],
  ["create_prehire", "rule", ["verify_offer_approval"], "employee.create"],
  ["compute_terms", "rule", ["create_prehire"], "hr.terms.compute"],
  ["request_documents_and_consent", "connector", ["compute_terms"], "documents.request"],
  ["await_documents", "wait", ["request_documents_and_consent"], null],
  ["extract_documents", "skill", ["await_documents"], "documents.extract"],
  ["cross_check", "rule", ["extract_documents"], "employee.verify"],
  ["activate_employee_id", "rule", ["cross_check"], "employee.activate_code"],
  ["provision_identity", "connector", ["activate_employee_id"], "directory.account.create"],
  ["select_laptop_spec", "rule", ["activate_employee_id"], "assets.laptop.select"],
  ["order_laptop", "connector", ["select_laptop_spec"], "assets.laptop.allocate"],
  ["issue_access_card", "connector", ["activate_employee_id"], "access.card.issue"],
  ["setup_payroll", "connector", ["activate_employee_id"], "payroll.employee.setup"],
  ["register_statutory", "connector", ["activate_employee_id"], "statutory.pf.register"],
  ["assign_buddy", "rule", ["activate_employee_id"], "employee.buddy.assign"],
  ["notify_manager", "skill", ["assign_buddy"], "comms.manager_brief"],
  [
    "schedule_day_one",
    "connector",
    ["provision_identity", "order_laptop", "issue_access_card", "setup_payroll", "register_statutory"],
    "calendar.events.create",
  ],
  ["policy_acknowledgements", "wait", ["schedule_day_one"], null],
  ["readiness_gate", "rule", ["policy_acknowledgements", "notify_manager"], "employee.activate"],
];

function steps(statuses: Partial<Record<string, StepStatus>>, fallback: StepStatus): Step[] {
  return ONBOARDING.map(([step_id, kind, needs, action], index) => {
    const status = statuses[step_id] ?? fallback;
    const started = status !== "PENDING" && status !== "READY";
    const done = status === "SUCCEEDED";
    const usesModel = kind === "skill" && done;
    return {
      step_id,
      kind,
      status,
      needs,
      attempt: started ? 1 : 0,
      human_touched: false,
      started_at: started ? minutesAgo(90 - index * 3) : null,
      finished_at: done ? minutesAgo(89 - index * 3) : null,
      cost_inr: usesModel ? "0.0420" : "0.0000",
      action,
      decision:
        started && action
          ? {
              effect: status === "WAITING_HUMAN" ? "REQUIRE_HUMAN" : "ALLOW",
              rule_ids: status === "WAITING_HUMAN" ? ["laptop_high_spend"] : ["allow"],
              policy_version: "approvals@1,rbac@1,reserved@1",
              reason:
                status === "WAITING_HUMAN"
                  ? "Laptops above ₹1.5L need IT spend approval [TBO IT Asset Policy §2.3]"
                  : "all policy checks passed",
            }
          : null,
      model_id: usesModel ? "mock/deterministic" : null,
      prompt_version: usesModel ? "v1" : null,
      error: status === "PARKED" ? "assets.allocate_laptop: HTTP 503" : null,
    };
  });
}

const allDone = Object.fromEntries(ONBOARDING.map(([id]) => [id, "SUCCEEDED" as StepStatus]));

function buildRun(
  id: string,
  subject_label: string,
  status: RunDetail["status"],
  stepList: Step[],
  started: number,
): RunDetail {
  const done = stepList.filter((s) => s.status === "SUCCEEDED").length;
  return {
    id,
    definition_name: "onboarding",
    definition_version: 1,
    subject_label,
    status,
    started_at: minutesAgo(started),
    completed_at: status === "completed" ? minutesAgo(started - 42) : null,
    steps_total: stepList.length,
    steps_done: done,
    cost_inr: stepList.reduce((sum, s) => sum + Number(s.cost_inr), 0).toFixed(4),
    steps: stepList,
    timeline: stepList
      .filter((s) => s.started_at)
      .map((s, index) => ({
        seq: index + 1,
        ts: s.started_at as string,
        actor_type: s.kind === "skill" ? ("agent" as const) : ("system" as const),
        actor_id: s.kind === "skill" ? `agent:${s.step_id}` : "system:engine",
        action: s.action ?? `wait.${s.step_id}`,
        effect: s.decision?.effect ?? null,
        step_id: s.step_id,
        reasoning: s.decision?.reason ?? null,
      })),
  };
}

const metrics: MetricsSummary = {
  completed_runs: 25,
  steps_completed: 475,
  subject_actions: 50,
  step_automation_rate: 0.914,
  straight_through_rate: 0.76,
  human_escalation_rate: 0.16,
  cycle_time_p50_seconds: 2520,
  cycle_time_p90_seconds: 5880,
  subject_wait_p50_seconds: 14400,
  variable_cost_inr: "3.1500",
  infra_alloc_inr: "250.0000",
  cost_per_workflow_inr: "10.1260",
  variable_cost_per_workflow_inr: "0.1260",
  runs_by_status: { completed: 25, running: 3, waiting: 2, paused: 0, failed: 1 },
  by_workflow: {},
  monthly_projection_inr: "12025.2000",
  projection_note:
    "Projection, not a measurement: measured variable cost per run x config/volumes.yaml + config/infra_costs.yaml.",
};

const APPROVALS_V1 = `pack: approvals
version: 1
owner: hr_admin
rules:
  - id: laptop_high_spend
    description: Laptops above Rs 1.5L need IT spend approval
    applies_to: ["assets.laptop.allocate"]
    when: "attributes.amount_inr > 150000"
    effect: REQUIRE_HUMAN
    approver_role: approver_it_spend
    source: "TBO IT Asset Policy 2.3"
`;

const RESERVED_V1 = `pack: reserved
version: 1
owner: hr_admin
reserved:
  - action: employment.probation.confirm
    approver_role: manager
route_only:
  - "grievance.*"
  - "posh.*"
  - "whistleblower.*"
`;

export function createFixtureApi(): RelayApi {
  const runs: RunDetail[] = [
    buildRun("run-0031", "Aarav Mehta · Senior Engineer", "running", steps({
      ...allDone,
      order_laptop: "RUNNING",
      schedule_day_one: "PENDING",
      policy_acknowledgements: "PENDING",
      readiness_gate: "PENDING",
    }, "SUCCEEDED"), 95),
    buildRun("run-0030", "Diya Kapoor · Product Manager", "waiting", steps({
      ...allDone,
      order_laptop: "WAITING_HUMAN",
      schedule_day_one: "PENDING",
      policy_acknowledgements: "PENDING",
      readiness_gate: "PENDING",
    }, "SUCCEEDED"), 180),
    buildRun("run-0029", "Vivaan Reddy · Analyst", "waiting", steps({
      ...allDone,
      cross_check: "WAITING_HUMAN",
      activate_employee_id: "PENDING",
      provision_identity: "PENDING",
      select_laptop_spec: "PENDING",
      order_laptop: "PENDING",
      issue_access_card: "PENDING",
      setup_payroll: "PENDING",
      register_statutory: "PENDING",
      assign_buddy: "PENDING",
      notify_manager: "PENDING",
      schedule_day_one: "PENDING",
      policy_acknowledgements: "PENDING",
      readiness_gate: "PENDING",
    }, "SUCCEEDED"), 240),
    buildRun("run-0028", "Ishaan Bose · Designer", "completed", steps(allDone, "SUCCEEDED"), 600),
    buildRun("run-0027", "Sara Menon · QA Engineer", "failed", steps({
      ...allDone,
      setup_payroll: "FAILED",
      schedule_day_one: "PENDING",
      policy_acknowledgements: "PENDING",
      readiness_gate: "PENDING",
    }, "SUCCEEDED"), 900),
  ];

  const tasks: HumanTask[] = [
    {
      id: "task-101",
      run_id: "run-0029",
      step_id: "cross_check",
      kind: "conflict",
      action: "employee.verify",
      title: "PAN name does not match the offer",
      assignee_role: "hr_ops",
      status: "open",
      options: ["accept_document_name", "request_new_document", "reject"],
      sla_due_at: minutesAhead(240),
      context_packet: {
        summary:
          "The name on the PAN card differs from the offer letter by one word. Every other field matches. This usually means a middle name was dropped.",
        likely_cause: "Middle name missing from the offer record",
        suggested_option: "accept_document_name",
        facts: {
          "Name on offer": "Vivaan Reddy",
          "Name on PAN": "Vivaan K Reddy",
          "Date of birth": "Matches",
          "Joining date": "Matches",
        },
        rule: "rules.hr.cross_check_identity",
      },
      decision: null,
      reason: null,
    },
    {
      id: "task-102",
      run_id: "run-0030",
      step_id: "order_laptop",
      kind: "approval",
      action: "assets.laptop.allocate",
      title: "Laptop above the spend threshold",
      assignee_role: "approver_it_spend",
      status: "open",
      options: ["approve", "downgrade_spec", "reject"],
      sla_due_at: minutesAhead(60),
      context_packet: {
        summary:
          "A product manager's role table maps to a laptop costing ₹2,10,000, above the ₹1,50,000 limit.",
        suggested_option: "approve",
        facts: { SKU: "LAT-16-PRO", Cost: "₹2,10,000", Limit: "₹1,50,000", Grade: "L6" },
        rule: "approvals@1 · laptop_high_spend",
      },
      decision: null,
      reason: null,
    },
    {
      id: "task-103",
      run_id: "run-0026",
      step_id: "probation_confirmation",
      kind: "reserved_decision",
      action: "employment.probation.confirm",
      title: "Confirm probation",
      assignee_role: "manager",
      status: "open",
      options: ["confirm", "extend", "refer_to_hr"],
      sla_due_at: minutesAgo(30),
      context_packet: {
        summary: "Probation ended yesterday. This decision is reserved for a named human.",
        facts: { Employee: "Neha Gupta", Grade: "L4", "Probation end": "Yesterday" },
        rule: "reserved@1 · employment.probation.confirm",
      },
      decision: null,
      reason: null,
    },
  ];

  const policies: Record<string, PolicyPack> = Object.fromEntries(
    [
      ["approvals", APPROVALS_V1],
      ["reserved", RESERVED_V1],
    ].map(([pack, content]) => [
      pack,
      {
        pack,
        version: 1,
        owner: "hr_admin",
        activated_by: "system:bootstrap",
        activated_at: minutesAgo(3000),
        content,
        draft: null,
        history: [{ version: 1, activated_by: "system:bootstrap", activated_at: minutesAgo(3000) }],
      },
    ]),
  );

  let kills: KillSwitch[] = [];
  let faults: FaultRule[] = [];
  const connectors = [
    "directory",
    "assets",
    "access",
    "payroll",
    "ticketing",
    "statutory",
    "calendar",
    "notify",
  ];

  const wait = <T,>(value: T): Promise<T> =>
    new Promise((resolve) => setTimeout(() => resolve(structuredClone(value)), 120));

  const versionOf = (content: string) => Number(/^version:\s*(\d+)/m.exec(content)?.[1] ?? NaN);

  return {
    isDemoData: true,
    demoUsers: () => wait(USERS),
    login: (email) => {
      const user = USERS.find((u) => u.email === email);
      if (!user) return Promise.reject(new Error("Unknown demo user."));
      return wait({ token: "fixture-token", user });
    },
    metrics: () => wait(metrics),
    runs: (status) => {
      const list: RunSummary[] = runs.filter((r) => !status || r.status === status);
      return wait(list);
    },
    run: (id) => {
      const run = runs.find((r) => r.id === id);
      return run ? wait(run) : Promise.reject(new Error(`Run ${id} was not found.`));
    },
    tasks: () => wait(tasks.filter((t) => t.status === "open" || t.status === "claimed")),
    decideTask: (id, option, reason) => {
      const task = tasks.find((t) => t.id === id);
      if (!task) return Promise.reject(new Error("Task not found."));
      task.status = "decided";
      task.decision = option;
      task.reason = reason;
      return wait(task);
    },
    policies: () =>
      wait(
        Object.values(policies).map(({ pack, version, owner, activated_by, activated_at }) => ({
          pack,
          version,
          owner,
          activated_by,
          activated_at,
        })),
      ),
    policy: (pack) =>
      policies[pack] ? wait(policies[pack]) : Promise.reject(new Error("Pack not found.")),
    saveDraft: (pack, content) => {
      policies[pack].draft = content;
      return wait(undefined);
    },
    testPolicy: (pack) => {
      const { draft, version } = policies[pack];
      const content = draft ?? policies[pack].content;
      return wait([
        { name: "pack parses", passed: content.startsWith(`pack: ${pack}`), detail: null },
        {
          name: "version increases",
          passed: !draft || versionOf(content) > version,
          detail: draft && versionOf(content) <= version ? `Active version is ${version}.` : null,
        },
        { name: "baseline floor is untouched", passed: true, detail: null },
      ]);
    },
    activatePolicy: (pack) => {
      const entry = policies[pack];
      if (entry.draft) {
        entry.content = entry.draft;
        entry.version = versionOf(entry.draft);
        entry.draft = null;
      }
      entry.activated_by = "hr.admin@tbo-demo.test";
      entry.activated_at = new Date().toISOString();
      entry.history.unshift({
        version: entry.version,
        activated_by: entry.activated_by,
        activated_at: entry.activated_at,
      });
      return wait(entry);
    },
    killSwitches: () => wait(kills),
    setKillSwitch: (scope, target, active, note) => {
      const entry: KillSwitch = {
        scope,
        target,
        active,
        note: note ?? null,
        set_by: "hr.admin@tbo-demo.test",
        set_at: new Date().toISOString(),
      };
      kills = [entry, ...kills.filter((k) => k.scope !== scope || k.target !== target)];
      return wait(entry);
    },
    connectorHealth: () => {
      const down = new Set(faults.filter((f) => f.fault === "503").map((f) => f.system));
      const list: ConnectorHealth[] = connectors.map((connector) => ({
        connector,
        healthy: !down.has(connector),
        status_code: down.has(connector) ? 503 : 200,
        latency_ms: 12,
        detail: null,
      }));
      return wait(list);
    },
    faults: () => wait(faults),
    addFault: (system, fault) => {
      const rule: FaultRule = { id: `f${faults.length + 1}`, system, op: "*", fault, remaining: null };
      faults = [...faults, rule];
      return wait(rule);
    },
    clearFaults: () => {
      faults = [];
      return wait(undefined);
    },
  };
}
