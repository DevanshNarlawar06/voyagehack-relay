// Invented data for building the screens before the API exists. Nothing here is measured, and the
// shell labels it as such. All people and identifiers are synthetic.
import type {
  DataRequest,
  AuditEvent,
  AuditEventDetail,
  ConnectorHealth,
  Definition,
  EmployeeRecord,
  EmployeeSummary,
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
  { email: "manager@tbo-demo.test", display_name: "Arjun Rao", roles: ["manager"] },
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

const PROBATION: [string, StepKind, string[], string | null][] = [
  ["wait_for_probation_end", "wait", [], "employment.probation.schedule"],
  ["probation_confirmation", "human", ["wait_for_probation_end"], "employment.probation.confirm"],
];

const EXIT: [string, StepKind, string[], string | null][] = [
  ["route_resignation", "rule", [], "employment.resignation.route"],
  ["accept_resignation", "human", ["route_resignation"], "employment.resignation.accept"],
  ["compute_exit_terms", "rule", ["accept_resignation"], "hr.exit_terms.compute"],
];

type Plan = [string, StepKind, string[], string | null][];

function steps(
  statuses: Partial<Record<string, StepStatus>>,
  fallback: StepStatus,
  plan: Plan = ONBOARDING,
): Step[] {
  return plan.map(([step_id, kind, needs, action], index) => {
    const status = statuses[step_id] ?? fallback;
    const started = status !== "PENDING" && status !== "READY";
    const done = status === "SUCCEEDED";
    const usesModel = kind === "skill" && done;
    const laptop = action === "assets.laptop.allocate";
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
              rule_ids: status !== "WAITING_HUMAN" ? ["allow"] : laptop ? ["laptop_high_spend"] : ["reserved"],
              policy_version: "approvals@1,rbac@1,reserved@1",
              reason:
                status !== "WAITING_HUMAN"
                  ? "all policy checks passed"
                  : laptop
                    ? "Laptops above ₹1.5L need IT spend approval [TBO IT Asset Policy §2.3]"
                    : `${action} is a reserved decision for a named person`,
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
  definition_name = "onboarding",
): RunDetail {
  const done = stepList.filter((s) => s.status === "SUCCEEDED").length;
  return {
    id,
    definition_name,
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
    {
      ...buildRun("run-0026", "Neha Gupta · Analyst", "waiting", steps({
        probation_confirmation: "WAITING_HUMAN",
      }, "SUCCEEDED", PROBATION), 1500, "onboarding_probation"),
      parent_run_id: "run-0024",
      parent: { id: "run-0024", definition_name: "onboarding", status: "completed", subject_label: "Neha Gupta · Analyst" },
    },
    {
      ...buildRun("run-0024", "Neha Gupta · Analyst", "completed", steps(allDone, "SUCCEEDED"), 9000),
      children: [
        { id: "run-0026", definition_name: "onboarding_probation", status: "waiting", subject_label: "Neha Gupta · Analyst" },
      ],
    },
    buildRun("run-0033", "Kiran Rao · Analyst", "waiting", steps({
      accept_resignation: "WAITING_HUMAN",
      compute_exit_terms: "PENDING",
    }, "SUCCEEDED", EXIT), 45, "exit"),
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
    {
      id: "task-104",
      run_id: "run-0033",
      step_id: "accept_resignation",
      kind: "reserved_decision",
      action: "employment.resignation.accept",
      title: "Accept resignation",
      assignee_role: "manager",
      status: "open",
      options: ["accept", "discuss_retention"],
      sla_due_at: minutesAhead(1440),
      context_packet: {
        summary: "Kiran Rao resigned today and asked for 18 December as the last day.",
        facts: { Employee: "Kiran Rao", Grade: "G2", "Notice period": "45 days", "Requested last day": "18 Dec" },
        rule: "baseline · employment.resignation.accept",
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
        draft_tests: null,
        history: [{ version: 1, activated_by: "system:bootstrap", activated_at: minutesAgo(3000) }],
      },
    ]),
  );

  const events: AuditEventDetail[] = [
    {
      id: "evt-3",
      seq: 3,
      ts: minutesAgo(5),
      actor_type: "agent",
      actor_id: "agent:document_extractor",
      on_behalf_of: "run.subject",
      action: "documents.extract",
      run_id: "run-0029",
      effect: "ALLOW",
      workflow_def_version: "onboarding@1",
      policy_version: "approvals@1,rbac@1,reserved@1",
      skill_version: "1",
      prompt_version: "v1",
      model_id: "mock/deterministic",
      input_hash: "9f2c41ab77d0",
      reasoning: "Validators failed on one field, so the model was asked.",
      prev_hash: "7bd2",
      hash: "c019",
      payload: {
        id: "pay-1",
        direction: "out",
        masked_text: "Document type: pan\nName: <PERSON_1>\nPAN: <PAN_1>\nDOB: <DOB_1>",
        provider: "mock",
        model_id: "mock/deterministic",
        region: "local",
        token_count: 18,
      },
    },
    {
      id: "evt-2",
      seq: 2,
      ts: minutesAgo(12),
      actor_type: "system",
      actor_id: "engine",
      on_behalf_of: "pool:hr_ops",
      action: "policy.authorize:assets.laptop.allocate",
      run_id: "run-0030",
      effect: "REQUIRE_HUMAN",
      workflow_def_version: "onboarding@1",
      policy_version: "approvals@1,rbac@1,reserved@1",
      skill_version: null,
      prompt_version: null,
      model_id: null,
      input_hash: null,
      reasoning: "Laptops above ₹1.5L need IT spend approval",
      prev_hash: "00aa",
      hash: "7bd2",
      payload: null,
    },
    {
      id: "evt-1",
      seq: 1,
      ts: minutesAgo(20),
      actor_type: "human",
      actor_id: "human:hr.admin@tbo-demo.test",
      on_behalf_of: null,
      action: "policy.authorize:audit.read",
      run_id: null,
      effect: "DENY",
      workflow_def_version: null,
      policy_version: "rbac@1",
      skill_version: null,
      prompt_version: null,
      model_id: null,
      input_hash: null,
      reasoning: "roles do not permit audit.read",
      prev_hash: "0000",
      hash: "00aa",
      payload: null,
    },
  ];
  const definitions: Definition[] = [
    { name: "onboarding", version: 1, status: "active", content_hash: "4e1f9a7c22b0d1", activated_by: "hr.admin@tbo-demo.test", activated_at: minutesAgo(4000) },
    { name: "exit", version: 1, status: "draft", content_hash: "b7700ce1a3f9e2", activated_by: null, activated_at: null },
  ];
  const employees: EmployeeRecord[] = [
    {
      id: "emp-1",
      full_name: "Aarav Mehta",
      employee_code: "TBO1042",
      state: "onboarding",
      role_title: "Senior Engineer",
      department: "Engineering",
      lifecycle: [{ id: "l1", event_type: "pre_hire_created", at: minutesAgo(95) }],
      documents: [{ id: "d1", category: "pan", retention_until: "2034-01-01" }],
      consents: [{ purpose_id: "onboarding_verification", notice_version: "v1", withdrawn_at: null }],
      data_requests: [],
    },
    {
      id: "emp-2",
      full_name: "Neha Gupta",
      employee_code: "TBO0981",
      state: "active",
      role_title: "Analyst",
      department: "Finance",
      lifecycle: [],
      documents: [],
      consents: [],
      data_requests: [{ id: "r1", type: "access", status: "open" }],
    },
  ];

  const dataRequests: DataRequest[] = [
    {
      id: "r1",
      type: "access",
      status: "completed",
      run_id: null,
      person_name: "Neha Gupta",
      created_at: minutesAgo(2880),
    },
  ];

  const usedTokens = new Set<string>();
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
    runs: (status, workflow) => {
      const list: RunSummary[] = runs.filter(
        (r) => (!status || r.status === status) && (!workflow || r.definition_name === workflow),
      );
      return wait(list);
    },
    run: (id) => {
      const run = runs.find((r) => r.id === id);
      return run ? wait(run) : Promise.reject(new Error(`Run ${id} was not found.`));
    },
    tasks: () => wait(tasks.filter((t) => t.status === "open" || t.status === "claimed")),
    decideTask: (id, option, reason, output) => {
      const task = tasks.find((t) => t.id === id);
      if (!task) return Promise.reject(new Error("Task not found."));
      if (output?.release_date && output?.buyout_agreed) {
        return Promise.reject(new Error("Agree a release date or a buyout, not both."));
      }
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
    auditEvents: (filters) =>
      wait(
        events
          .filter(
            (e) =>
              (!filters.actor || e.actor_id === filters.actor) &&
              (!filters.action || e.action.includes(filters.action)) &&
              (!filters.run_id || e.run_id === filters.run_id) &&
              (!filters.effect || e.effect === filters.effect) &&
              (filters.before_seq === undefined || e.seq < filters.before_seq),
          )
          .map((e): AuditEvent => e),
      ),
    auditEvent: (id) => {
      const event = events.find((e) => e.id === id);
      return event ? wait(event) : Promise.reject(new Error("Event not found."));
    },
    chat: (text) => {
      if (/probation/i.test(text)) {
        return wait({
          intent: "policy_question",
          reply: "During probation you cannot use earned leave. Casual and sick leave can be used.",
          citations: [{ id: "leave.probation", title: "Leave during probation", source: "TBO Leave Policy §1-4 (placeholder)" }],
          routed_to_hr: false,
        });
      }
      return wait({ intent: "unknown", reply: "I've passed this to HR, and someone will get back to you.", citations: [], routed_to_hr: true });
    },
    uploadDocuments: (token, files) => {
      if (usedTokens.has(token)) {
        return Promise.reject(new Error("This upload link is not valid. Please ask HR for a new one."));
      }
      usedTokens.add(token);
      return wait({ documents: files.map((f, i) => ({ id: `doc-${i}`, category: f.category })), resumed: true });
    },
    verifyAudit: () => wait({ ok: true, checked: events.length, first_bad_seq: null, problem: null }),
    definitions: () => wait(definitions),
    employees: () => wait(employees.map((e): EmployeeSummary => e)),
    employee: (id) => {
      const employee = employees.find((e) => e.id === id);
      return employee ? wait(employee) : Promise.reject(new Error("Employee not found."));
    },
    dataRequests: () => wait(dataRequests),
    raiseDataRequest: (type) => {
      const row: DataRequest = {
        id: `r${dataRequests.length + 1}`,
        type,
        status: "in_progress",
        run_id: null,
        person_name: "Meera Nair",
        created_at: new Date().toISOString(),
      };
      dataRequests.unshift(row);
      return wait({ id: row.id, run_id: row.run_id });
    },
  };
}
