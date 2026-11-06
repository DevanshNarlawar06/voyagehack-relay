# Relay: Product Requirements Document

**AI-native HR Services Platform for TBO.com (Darwinbox replacement track)**

| Field | Value |
|---|---|
| Document owner | CTO (Sarthak Viche) |
| Status | v1.1, approved for build (v1.1 changes: see Section 0) |
| Date | 6 Nov 2026 (v1.1: 7 Nov 2026) |
| Audience | Engineering team, coding agents, Round 1 reviewers |
| Vertical 1 (depth) | Employee Onboarding |
| Vertical 2 (extension proof) | Employee Exit |

> **How coding agents should use this document.** Sections 1 to 6 explain *why* and *what*. Sections 7 to 20 are the build contract: tech stack, repo layout, schemas, interfaces and invariants. Section 23 splits the build into work packages, each with dependencies and acceptance criteria. When this document and your instinct disagree, follow the document and raise a question in `docs/decisions/`. **Never** weaken an invariant from Section 6.

---

## 0. Changes in v1.1 (7 Nov 2026)

Gaps found by re-reading the TBO problem statement (`docs/problem-statements/PS6-darwinbox-hr.txt`) against v1.0, plus changes needed to build this with two parallel coding sessions:

1. **Gratuity and leave added to labour rules** (Section 12.5). The brief names "PF, gratuity, leave rules" explicitly; v1.0 only covered notice, probation, F&F and PF.
2. **Fair hiring added to the baseline floor** (Section 12.3). `hiring.candidate.reject` and `hiring.candidate.shortlist_decision` are now baseline-reserved, and any candidate ranking must store per-candidate reasons. Onboarding does not rank candidates, but the live extension test may ask for a talent-acquisition flow.
3. **Offline-first development** (new Section 27.4). A deterministic `mock` model provider so the whole system, CI and the demo run with no API keys. Real providers are a config switch.
4. **TBO starter kit not yet received** (Section 25). We build against our own synthetic fixtures behind an adapter in `seed/` and `mocks/`, shaped like the brief's description, and swap in TBO's kit when it arrives.
5. **Heavy dependencies have light fallbacks** (Section 27.4): regex India recognizers before Presidio, text-layer synthetic documents before PaddleOCR, web chat before Slack.
6. **Priorities on every work package** (Section 27.2): P0 (demo-critical) / P1 (scored) / P2 (polish). Build the P0 slice end to end before any P1.
7. **Two-session delivery plan and collaboration protocol** (Section 27). Work packages are split between Session A and Session B; coordination runs through `CONVERSATION.md`, GitHub Issues and PRs (`docs/collab/PROTOCOL.md`).

## 1. Purpose

Today an HRMS *records* HR events, but people still *move* the work. When a hire is made, HR emails IT for a laptop, Admin for an access card and Finance for payroll setup, then chases each one by hand.

Relay is a **governed workflow runtime for HR**. Agents complete routine work across teams from start to finish, humans make every decision with legal, financial or human consequences, and every action is policy-checked, audited and costed in INR.

**One-line positioning:** Darwinbox gives you agents inside a platform you rent. Relay gives TBO a governed HR runtime it owns, where every agent action is priced, policy-checked and auditable, and where new service lines are configuration, not rewrites.

## 2. Problem statement (from TBO brief, condensed)

Build an agent-first HR services platform that could replace a conventional HRMS. All eight objectives are mandatory:

1. **Automate end to end.** Close cross-team loops (IT, Admin, Finance, hiring manager) without manual emails or follow-ups, and report the share of steps with no human touch.
2. **Platform, not point solution.** Developers add service lines, agents, workflows and integrations without changing the core, using a documented extension model.
3. **Humans on decisions that matter.** Reserved decisions go to a named human, and the reserved list is HR-editable configuration.
4. **Comply by design.** Policy and legal rules live in a versioned, testable, auditable layer, not in prompts.
5. **Clean system of record.** The full lifecycle data model, RBAC, and a complete audit trail of who or what changed what.
6. **Fail safely.** Uncertainty, outages and conflicts go to a human queue with context attached. Retries never duplicate actions.
7. **Serve people where they work.** A conversational interface (Slack or Teams, plus web).
8. **Prove it with numbers.** A dashboard of cost per completed workflow, automation rate, cycle time and escalation rate.

**Reserved for humans (minimum set):** resignation acceptance and release date or buyout; final hiring decisions, offer approval and compensation; termination, discipline and PIPs; salary, grade or role changes and policy exceptions; POSH, grievance and whistleblower cases (route only); sign-off on statutory filings and payroll runs.

**Also required:** DPDP Act 2023 compliance, PII masking before external model calls, fair hiring, explainability per action, RBAC (an agent never exceeds its principal), a kill switch, and labour law encoded as rules.

**Cost rule:** cost per workflow is measured, not estimated. Rules go in code. Small models are the default, with a justified escalation threshold. Provider changes are config-only. All infrastructure is counted.

**Rounds:** (1) a 6-page architecture doc plus a 5-minute video; (2) a working build on the synthetic dataset with repo, README and developer guide; (3) a live demo plus a **live extension test**.

## 3. Goals, non-goals, success metrics

### 3.1 Goals mapped to the rubric

| Rubric criterion (weight) | What Relay must demonstrate | Primary evidence |
|---|---|---|
| Governance & compliance (20%) | Reserved decisions are impossible for agents. Policy is versioned config with tests. Hash-chained audit. DPDP features | Policy editor + test run, audit explorer, consent ledger, data-request workflow |
| Extensibility & architecture (20%) | Four extension points, core untouched. Exit workflow built purely as an extension. Live test done in under 30 minutes | `relay new workflow` scaffold, developer guide, git diff showing zero core changes for Exit |
| Automation depth (20%) | At least 85% of onboarding steps with no human touch across the full synthetic dataset, including messy cases | Metrics dashboard computed from events |
| Cost efficiency (15%) | Measured INR per onboarding, a model router with a threshold derived from an eval curve, a monthly projection | Cost dashboard, `evals/threshold_report.md` |
| Engineering quality (15%) | Exactly-once side effects under crash, tests, security | Live chaos demo, CI badge, test coverage |
| Experience & demo (10%) | Slack-first for employees and managers, a clean console for HR | 5-minute demo script (Section 22) |

### 3.2 Non-goals for the hackathon

These are out of scope: a full payroll engine (we call the payroll-setup mock and never compute payroll); a full ATS; multi-tenant SaaS; mobile apps; Microsoft Teams (Slack only, with Teams as a documented adapter); production SSO (demo auth with documented users); and real statutory filing integrations.

### 3.3 Metric definitions (canonical; the dashboard must use these exactly)

- **Step automation rate** = steps completed with `human_touched = false` ÷ total steps completed, across all completed runs. A *human touch* is any action by an HR, ops, IT, Admin, Finance or approver user that the step needs before it can finish. Data-subject actions (a new joiner uploading documents or giving consent) are **not** human touches. They are counted separately as `subject_actions`.
- **Straight-through rate** = runs with zero human touches other than reserved decisions ÷ completed runs.
- **Human escalation rate** = runs with at least one *non-reserved* human task (exception, conflict, low confidence, outage) ÷ completed runs.
- **Cycle time** = `run.completed_at - run.started_at`, reported as p50 and p90, excluding time waiting on data-subject actions (shown separately).
- **Cost per completed workflow (INR)** = Σ(variable LLM and OCR cost for the run) + allocated fixed infrastructure (monthly infra INR ÷ projected monthly runs). Section 17 has the full formula.

## 4. Users, roles, personas

| Role key | Persona | Primary surface | Can do |
|---|---|---|---|
| `new_joiner` | Incoming employee | Slack DM, web upload page | Upload documents, give consent, ask questions, view own onboarding status |
| `employee` | Existing employee | Slack | Ask policy questions, view own record, raise data requests |
| `manager` | Hiring or line manager | Slack | Approve manager-scoped items, see their team's onboarding, assign a buddy override |
| `hr_ops` | HR operations executive | Web console + Slack | Work the human queue, resolve conflicts, view runs |
| `hr_admin` | HR head / HRBP lead | Web console | Everything `hr_ops` can, plus edit and activate policy packs, toggle kill switches, approve reserved decisions |
| `approver_*` | Named approvers (`approver_offer`, `approver_it_spend`, `approver_finance`) | Slack cards + web | Decide the reserved or threshold decisions routed to their role |
| `it_ops`, `admin_ops`, `finance_ops` | Functional teams | Slack / mock systems | Handle tasks that connectors hand off for physical fulfilment (simulated) |
| `developer` | TBO in-house developer | CLI + git | Add workflows, skills, connectors and policy packs through PRs |
| `auditor` | Compliance reviewer | Web console | Read-only audit explorer and exports |
| `agent:*` | Service principals, one per skill | n/a | Act only on behalf of a principal; effective permissions = principal ∩ skill allowlist |

## 5. Scope

### 5.1 Vertical 1: Onboarding (built to production depth)

**Trigger:** a candidate reaches `offer_accepted` in the synthetic dataset, *and* a recorded human `hiring.offer.approve` decision exists. Without that decision, the run is refused with a `DENY` audit event.

**Steps** (kind in brackets; the canonical YAML is in Section 19):

| # | Step id | Kind | Description |
|---|---|---|---|
| 0 | `verify_offer_approval` | rule | Assert that the reserved decision `hiring.offer.approve` exists, signed by a named approver |
| 1 | `create_prehire` | rule | Create the employee record in `pre_hire` state from the candidate |
| 2 | `compute_terms` | rule | Joining date, probation end, notice terms and leave accrual start, from the `labour_rules` policy pack |
| 3 | `request_documents_and_consent` | connector (notify) | Send the joiner a secure upload link plus a DPDP consent notice listing purposes |
| 4 | `await_documents` | wait | Wait for a subject action. SLA reminders at +24h and +48h, then escalate to `hr_ops` at +72h |
| 5 | `extract_documents` | skill (`document_extractor`, local) | Local OCR → structured fields → deterministic validators |
| 6 | `cross_check` | rule | Name, DOB, PAN-name match and joining date versus the offer. A mismatch opens a `conflict` human task |
| 7 | `activate_employee_id` | rule | Allocate the employee ID and move to `onboarding` state |
| 8 | `provision_identity` | connector (`directory.create_account`) | Email and directory account |
| 9 | `select_laptop_spec` | rule | Spec from the role/grade table. If cost > threshold, `REQUIRE_HUMAN(approver_it_spend)` |
| 10 | `order_laptop` | connector (`assets.allocate_laptop`) | Idempotent allocation |
| 11 | `issue_access_card` | connector (`access.issue_card`) | Needs the photo from step 5 |
| 12 | `setup_payroll` | connector (`payroll.setup_employee`) | Uses the approved CTC verbatim. Never modifies compensation |
| 13 | `register_statutory` | connector (`statutory.register_pf`) | PF/UAN. A mock extension if TBO does not provide one |
| 14 | `assign_buddy` | rule | Same team, tenure > 6 months, lowest current buddy load |
| 15 | `notify_manager` | skill (`comms_drafter`) + connector (notify) | Small-model drafted welcome brief to the manager and buddy |
| 16 | `schedule_day_one` | connector (`calendar.create_events`) | Induction, IT handover, buddy intro |
| 17 | `policy_acknowledgements` | wait (subject action) | The joiner acknowledges required policies in Slack or on the web |
| 18 | `readiness_gate` | rule | All provisioning steps succeeded → state `active` |
| 19 | `schedule_probation_review` | wait (timer) → human (reserved) | At probation end, open a `employment.probation.confirm` task for the manager (reserved) |

**Cross-cutting during the run:** the `helpdesk` skill answers the joiner's questions in Slack with citations, and the SLA engine chases every pending connector task that has a fulfilment SLA.

### 5.2 Vertical 2: Exit (built only through extension points)

Trigger: an employee submits a resignation through Slack (`intake` skill). Steps: `route_resignation` → **human reserved** `employment.resignation.accept` (with release date / buyout) → `compute_last_day` (rule) → `knowledge_transfer_plan` (comms skill) → `schedule_access_revocation` (timer at last day) → `recover_assets` → `revoke_access_card` → `disable_directory` → `notify_payroll_exit` → `compute_fnf_inputs` (rule; F&F *inputs* only) → **human reserved** `payroll.fnf.signoff` → `issue_relieving_letter` (templated) → `exit_survey`.

**Acceptance rule:** the Exit vertical must be delivered with **zero changes** under `relay/core/`. CI verifies this on the Exit PR.

## 6. Architectural principles and invariants

These are hard rules. Every one is enforced by a test or an import-linter contract.

1. **Four step kinds, explicitly typed:** `rule` (pure code), `connector` (external side effect), `skill` (model-backed judgement), `human` (decision by a named person). Plus control steps `wait` and `parallel`. The orchestrator is never an LLM.
2. **Every step calls `authorize()` before executing.** No code path executes an action without a `Decision` row. (Enforced: the engine is the only caller of `StepExecutor.run`, and the executor requires a `Decision` argument.)
3. **Reserved decisions have a floor.** A baseline reserved set is compiled into `relay/core/policy/baseline.py` and cannot be removed by configuration. HR config can only *add* to it. A human-kind step for a reserved action can only be completed by a `human_task` decision from a user holding the configured approver role. Agents and system actors get `DENY` on reserved actions under all configurations.
4. **All external side effects go through connectors** with an idempotency ledger. No HTTP calls elsewhere. (Import-linter: only `relay.connectors.*` may import `httpx`.)
5. **All model calls go through the Model Router**, which goes through the PII Gateway. (Import-linter: only `relay.models.router` may import `litellm`.)
6. **The audit log is append-only and hash-chained.** There is no UPDATE or DELETE on `audit_events` (enforced by DB trigger plus revoked privileges for the app role).
7. **Agents act on behalf of a principal.** Effective permissions = `principal.permissions ∩ skill.allowed_actions`. A skill with no principal cannot act.
8. **Rules never live in prompts.** Prompts may receive *outputs* of rules as context. They never compute statutory values, dates, eligibility or routing.
9. **Kill switches are checked at every step boundary** and take effect within 1 second.
10. **Everything versioned is stamped.** Each audit event carries `workflow_def_version`, `policy_version`, and, when relevant, `skill_version`, `prompt_version` and `model_id`.

## 7. System architecture

### 7.1 Component diagram

```
                ┌───────────────┐      ┌────────────────────┐
  Employees ───►│  Slack App    │      │  Web Console       │◄─── HR / Approvers / Auditor
  Managers      │ (Bolt, Socket)│      │ (Next.js)          │
                └──────┬────────┘      └─────────┬──────────┘
                       │  REST/JSON (authN: demo JWT)│
                       ▼                            ▼
                ┌──────────────────────────────────────────┐
                │              Relay API (FastAPI)          │
                │  intake · runs · tasks · policy · audit   │
                │  metrics · definitions · data-requests     │
                └──────┬───────────────────────────┬───────┘
                       │ enqueue / read             │
                       ▼                            ▼
   ┌────────────────────────────────┐    ┌──────────────────────────┐
   │  Workflow Engine (workers ×N)   │    │  Policy Engine            │
   │  - DSL interpreter              │───►│  authorize(): CEL rules,  │
   │  - step leasing (SKIP LOCKED)   │    │  baseline reserved floor, │
   │  - timers / SLAs / retries      │    │  kill switches, RBAC      │
   │  - idempotency ledger           │    └──────────────────────────┘
   └───┬──────────────┬────────────┬┘
       │              │            │
       ▼              ▼            ▼
 ┌───────────┐ ┌─────────────┐ ┌──────────────────────────────┐
 │Connectors │ │ Human Queue │ │ Skills (agents)              │
 │ directory │ │ tasks,      │ │ intake, document_extractor,  │
 │ assets    │ │ approvals,  │ │ exception_analyst, comms,    │
 │ access    │ │ conflicts   │ │ helpdesk                     │
 │ payroll   │ └─────────────┘ └──────────────┬───────────────┘
 │ statutory │                                ▼
 │ calendar  │                     ┌──────────────────────┐
 │ notify    │                     │ Model Router (tiers) │
 └─────┬─────┘                     └──────────┬───────────┘
       ▼                                      ▼
 TBO mock APIs                     ┌──────────────────────┐
 (+ our extensions)                │ PII Gateway (mask /   │──► LLM providers via LiteLLM
                                   │ unmask, payload log)  │    (tier1 small, tier2 large)
                                   └──────────────────────┘
 Local: OCR (PaddleOCR), embeddings, pgvector

 PostgreSQL 16: system of record · runs · steps · tasks · ledger · audit (hash chain) · cost · pgvector
```

### 7.2 Request lifecycle (example: laptop order)

1. A worker leases `step_run(order_laptop)`.
2. The engine checks the kill switch for the global, workflow, step and connector scopes.
3. The engine builds an `ActionRequest(action="assets.laptop.allocate", actor=system:engine, on_behalf_of=hr_ops pool, ...)` and calls `authorize()`.
4. On `ALLOW`, the executor computes `idempotency_key = sha256(run_id:step_id:assets.laptop.allocate)`, then inserts a ledger row with `status=pending` (UNIQUE key).
5. The connector calls the mock with the `Idempotency-Key` header. If the mock lacks idempotency support, the connector first runs `reconcile(key)` (lookup by our reference) and skips the call if the item exists.
6. On success, write the ledger `external_id`, the step output, the audit event, and `cost_event` (zero variable cost).
7. On crash between steps 5 and 6, the lease expires, another worker re-leases the step, sees the ledger `pending`, calls `reconcile(key)`, finds the existing allocation, and completes **without a second order**.

## 8. Technology stack (decisions are final unless an ADR overrides them)

| Concern | Choice | Licence | Why |
|---|---|---|---|
| Language (backend) | Python 3.12 | PSF | Best ML/OCR/PII ecosystem, fast iteration, readable for TBO devs |
| Package/env | `uv` | MIT/Apache | Fast, reproducible lockfile |
| API | FastAPI + Pydantic v2 | MIT | Typed contracts, OpenAPI for free |
| ORM / migrations | SQLAlchemy 2.0 + Alembic | MIT | Mature, explicit |
| Database | PostgreSQL 16 + `pgvector` + `pgcrypto` | PostgreSQL | One store for records, queue, audit, vectors |
| Workflow engine | **In-house durable interpreter** on Postgres (`FOR UPDATE SKIP LOCKED` leases) | ours | Full control of the DSL, idempotency and audit semantics; every line explainable in the finale Q&A. DBOS Transact (MIT) is the documented fallback (ADR-002) |
| Expressions & policy conditions | CEL via `cel-python` | Apache-2.0 | Safe, non-Turing-complete, readable by non-engineers, used by Kubernetes |
| LLM abstraction | LiteLLM (SDK only, no proxy) | MIT | Provider swap = config change |
| OCR | PaddleOCR (CPU) | Apache-2.0 | Local, so documents never leave our infrastructure |
| PII detection | Microsoft Presidio + custom India recognizers | MIT | Pluggable recognizers for PAN, Aadhaar, IFSC, UAN |
| Embeddings | `bge-small-en-v1.5` local (sentence-transformers) | MIT | Free, small, good retrieval |
| Slack | `slack-bolt` (Socket Mode) | MIT | No public URL needed for the demo |
| Frontend | Next.js 15 (App Router) + TypeScript + Tailwind + shadcn/ui + TanStack Query | MIT | Fast, clean console |
| Charts | Recharts | MIT | Dashboard |
| Observability | OpenTelemetry SDK + structured JSON logs | Apache-2.0 | Traces per run |
| Quality | ruff, mypy `--strict`, pytest, hypothesis, import-linter, Playwright (smoke) | MIT/BSD/Apache | Enforce invariants |
| Packaging | Docker Compose (api, worker×2, web, slack, postgres, mocks) | n/a | One-command run |

**Model tiers (configured in `config/models.yaml`, never in code):**
- `tier0`: no model (rules).
- `tier1`: a small, low-cost hosted or open-weight model with structured output (e.g. an 8B-class open-weight model via an inference provider, or a provider's "flash/mini/haiku" class model). Final choice is made by the eval harness on cost × accuracy.
- `tier2`: a larger model, called only when the router's escalation condition fires.
- `local`: OCR and embeddings on CPU.

## 9. Repository layout

```
relay/
├─ README.md                     # setup, demo users, feature checklist vs objectives 1-8
├─ PRD.md                        # this document
├─ docker-compose.yml
├─ pyproject.toml                # uv workspace
├─ config/
│  ├─ models.yaml                # tiers, providers, prices (INR), thresholds
│  ├─ infra_costs.yaml           # monthly fixed costs for allocation
│  └─ app.yaml                   # SLAs defaults, lease durations, feature flags
├─ definitions/                  # ← EXTENSION SURFACE (no code changes needed)
│  ├─ workflows/
│  │  ├─ onboarding.v1.yaml
│  │  └─ exit.v1.yaml
│  ├─ policies/
│  │  ├─ reserved.yaml           # HR-extendable reserved actions
│  │  ├─ approvals.yaml          # thresholds & approver routing
│  │  ├─ labour_rules.yaml       # notice, probation, F&F timelines (cite source clauses)
│  │  ├─ rbac.yaml               # roles → permissions
│  │  ├─ retention.yaml          # DPDP retention per data category
│  │  └─ tests/*.yaml            # policy test cases
│  ├─ skills/*.yaml              # skill manifests (schema, tier, allowed_actions, prompt ref)
│  ├─ prompts/<skill>/<version>.md
│  └─ connectors/*.yaml          # connector manifests (base_url, ops, idempotency mode)
├─ relay/                        # Python package
│  ├─ core/                      # ← FROZEN for extensions
│  │  ├─ engine/                 # interpreter, scheduler, leasing, timers, retries
│  │  ├─ policy/                 # authorize(), CEL evaluator, baseline.py, loader
│  │  ├─ ledger/                 # idempotency ledger
│  │  ├─ audit/                  # hash-chained writer, verifier
│  │  ├─ cost/                   # cost events, allocation
│  │  ├─ registry/               # loads definitions, validates against JSON Schemas
│  │  └─ dsl/schema/*.json       # JSON Schemas for workflow/skill/connector/policy files
│  ├─ db/                        # models.py, migrations/
│  ├─ connectors/                # base.py + one module per connector family
│  ├─ skills/                    # base.py + one module per skill (thin; logic in manifests/prompts)
│  ├─ models/                    # router.py, pii_gateway.py, recognizers/
│  ├─ rules/                     # registered pure functions callable from rule steps
│  ├─ api/                       # FastAPI routers
│  ├─ slack/                     # Bolt app, intents, Block Kit cards
│  ├─ worker/                    # worker entrypoint
│  └─ cli/                       # `relay` CLI (Typer)
├─ web/                          # Next.js console
├─ mocks/                        # TBO-provided mocks + our extensions + fault injection
├─ seed/                         # synthetic dataset loaders + demo scenarios
├─ evals/                        # labelled sets, runner, threshold_report.md
├─ tests/
│  ├─ unit/  ├─ policy/  ├─ simulation/  ├─ chaos/  └─ e2e/
└─ docs/
   ├─ developer-guide.md         # how to add a workflow / skill / connector / policy
   ├─ architecture.md            # Round 1 source
   ├─ pii-inventory.md           # exactly which fields reach which model, where hosted
   └─ decisions/ADR-*.md
```

## 10. Data model (PostgreSQL)

All tables have `id UUID PK DEFAULT gen_random_uuid()`, `created_at timestamptz`, `updated_at timestamptz` (except where append-only). Sensitive columns marked 🔒 are encrypted at application level (AES-GCM, key from env) and never logged.

### 10.1 System of record

| Table | Key columns |
|---|---|
| `persons` | `full_name`, `email`, `phone`🔒, `dob`🔒, `pan`🔒, `aadhaar_last4`, `bank_account`🔒, `ifsc`, `photo_ref` |
| `employees` | `person_id FK`, `employee_code UNIQUE`, `state` ENUM(`pre_hire`,`onboarding`,`active`,`notice`,`exited`), `grade`, `role_title`, `department`, `location`, `manager_id FK`, `joining_date`, `probation_end`, `ctc_approved`🔒, `last_working_day` |
| `candidates` | from the dataset: `person_id`, `status`, `offer_id` |
| `offers` | `candidate_id`, `ctc`🔒, `grade`, `joining_date`, `approved_decision_id FK → human_tasks` |
| `lifecycle_events` | append-only: `employee_id`, `event_type`, `payload jsonb`, `run_id` |
| `documents` | `person_id`, `category` ENUM(`pan`,`aadhaar`,`bank_proof`,`education`,`photo`,`other`), `storage_ref` (encrypted at rest on local volume), `extracted jsonb`🔒, `validation jsonb`, `retention_until` |
| `consents` | `person_id`, `purpose_id`, `notice_version`, `granted_at`, `withdrawn_at`, `channel` |
| `data_requests` | `person_id`, `type` ENUM(`access`,`correction`,`erasure`), `status`, `run_id` |
| `users` | `email`, `display_name`, `slack_user_id`, `person_id NULL`, `roles text[]` |

### 10.2 Runtime

| Table | Key columns |
|---|---|
| `workflow_definitions` | `name`, `version`, `content_hash`, `yaml`, `status` (`draft`,`active`,`retired`), `activated_by`, `activated_at`. UNIQUE(name, version) |
| `workflow_runs` | `definition_name`, `definition_version`, `subject_type`, `subject_id`, `status` ENUM(`running`,`paused`,`waiting`,`completed`,`failed`,`cancelled`), `context jsonb`, `started_by_actor`, `started_at`, `completed_at`, `trace_id` |
| `step_runs` | `run_id`, `step_id`, `kind`, `status` (Section 11.3), `attempt int`, `next_attempt_at`, `lease_owner`, `lease_expires_at`, `input jsonb`, `output jsonb`, `error jsonb`, `human_touched bool`, `decision_id FK`, `started_at`, `finished_at`. UNIQUE(run_id, step_id) |
| `timers` | `step_run_id`, `due_at`, `purpose` (`wait`,`sla_reminder`,`sla_escalate`,`retry`), `fired_at`. Index on `(fired_at IS NULL, due_at)` |
| `idempotency_ledger` | `key UNIQUE`, `connector`, `operation`, `request_hash`, `status` (`pending`,`succeeded`,`failed_permanent`), `external_id`, `response jsonb`, `first_attempt_at`, `completed_at` |
| `human_tasks` | `run_id`, `step_run_id`, `kind` ENUM(`reserved_decision`,`approval`,`conflict`,`exception`,`low_confidence`,`outage`,`route_only`), `action`, `assignee_role`, `assignee_user_id NULL`, `context_packet jsonb`, `options jsonb`, `status` (`open`,`claimed`,`decided`,`expired`,`cancelled`), `decision` jsonb, `decided_by`, `decided_at`, `reason`, `sla_due_at` |
| `decisions` | output of `authorize()`: `request jsonb`, `effect`, `approver_role`, `rule_ids text[]`, `policy_version`, `reason` |
| `kill_switches` | `scope` ENUM(`global`,`workflow`,`step`,`skill`,`connector`), `target`, `active bool`, `set_by`, `set_at`, `note` |
| `policy_versions` | `pack`, `version`, `content_hash`, `content`, `tests_passed bool`, `activated_by`, `activated_at` |
| `conversations`, `messages` | Slack/web chat history (masked copies only), `intent`, `run_id` |

### 10.3 Audit and cost

| Table | Key columns |
|---|---|
| `audit_events` | **append-only**: `seq bigserial`, `ts`, `actor_type` (`human`,`agent`,`system`), `actor_id`, `on_behalf_of`, `action`, `subject_type`, `subject_id`, `run_id`, `step_run_id`, `effect`, `workflow_def_version`, `policy_version`, `skill_version`, `prompt_version`, `model_id`, `input_hash`, `masked_payload_ref`, `reasoning text`, `data jsonb`, `prev_hash`, `hash` (sha256 of row content + prev_hash) |
| `model_payloads` | `id`, `direction` (`out`,`in`), `masked_text`, `provider`, `region`, `token_count`. What actually left our infrastructure, byte for byte |
| `cost_events` | `run_id`, `step_run_id`, `kind` (`llm`,`ocr_cpu`,`embedding_cpu`,`infra_alloc`), `provider`, `model_id`, `tokens_in`, `tokens_out`, `unit_prices jsonb`, `cpu_seconds`, `cost_inr numeric(12,4)` |

DB protections: a trigger rejects UPDATE/DELETE on `audit_events`, `lifecycle_events` and `model_payloads`. The app DB role has INSERT/SELECT only on them. `relay audit verify` recomputes the chain.

## 11. Workflow engine specification

### 11.1 DSL (YAML, validated against `relay/core/dsl/schema/workflow.schema.json`)

```yaml
name: onboarding            # unique
version: 1                  # integer, immutable once active
subject: employee           # subject type the run is about
trigger:
  event: candidate.offer_accepted
  require_decision: hiring.offer.approve      # reserved decision must exist
context:                    # initial context bindings (CEL over trigger payload)
  candidate_id: "trigger.candidate_id"
defaults:
  retry: { max_attempts: 5, backoff: exponential, base_seconds: 10, max_seconds: 600 }
  timeout_seconds: 120
steps:
  - id: compute_terms
    kind: rule
    uses: rules.labour.compute_onboarding_terms     # registered pure function
    action: hr.terms.compute                        # action name for authorize()
    needs: [create_prehire]
    inputs:
      grade: "ctx.offer.grade"
      joining_date: "ctx.offer.joining_date"
    outputs:
      terms: "result"                               # bind into ctx.terms

  - id: order_laptop
    kind: connector
    uses: assets.allocate_laptop
    action: assets.laptop.allocate
    needs: [select_laptop_spec]
    when: "ctx.laptop.approved == true"
    inputs:
      employee_code: "ctx.employee.code"
      sku: "ctx.laptop.sku"
    sla: { fulfil_within: 24h, remind_at: [12h], escalate_to: it_ops }
    on_failure: park                                # park | fail | compensate:<step>

  - id: probation_confirmation
    kind: human
    action: employment.probation.confirm            # reserved via policy
    needs: [schedule_probation_review]
    task:
      assignee_role: manager
      assignee_user: "ctx.employee.manager_user_id"
      options: [confirm, extend, refer_to_hr]
      context_from: [ctx.employee, ctx.terms]
```

**Step kinds:** `rule`, `connector`, `skill`, `human`, `wait` (`until: <CEL timestamp>` or `for_subject_action: <action>`), `parallel` (`branches: [[step ids]]`). Dependencies come from `needs`. Steps with satisfied `needs` and a true `when` become `READY`. A false `when` makes the step `SKIPPED`.

**Expressions:** CEL only, evaluated against `{ctx, trigger, result, now}`. No arbitrary Python in YAML. Rule logic lives in registered functions in `relay/rules/`, each with a docstring citing its policy source.

**Registry validation at load:** schema validity; every `uses` resolves to a registered rule, connector op or skill; every `action` is declared in `rbac.yaml`; no cycles; every `human` step for a baseline-reserved action has an `assignee_role` that holds the approval permission. Invalid definitions cannot be activated.

### 11.2 Scheduler and leasing

- Workers poll: `SELECT ... FROM step_runs WHERE status='READY' AND (next_attempt_at IS NULL OR next_attempt_at<=now()) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT :n`, then set `status='RUNNING', lease_owner, lease_expires_at = now()+lease`.
- Heartbeat extends the lease every `lease/3`. Expired leases are reclaimed by a reaper loop (status back to `READY`, `attempt` unchanged; reclaim is not a failure).
- The timer loop fires due `timers` rows and transitions their steps.
- Use `LISTEN/NOTIFY` on `step_ready` for low latency, with polling every 1s as a fallback.
- Concurrency: at least 2 workers in Compose to prove safety.

### 11.3 Step state machine

```
PENDING ─(needs met & when=true)→ READY ─(lease)→ RUNNING
RUNNING → SUCCEEDED
RUNNING → WAITING_HUMAN     (Decision=REQUIRE_HUMAN, or kind=human, or park)
RUNNING → WAITING_TIMER     (kind=wait)
RUNNING → RETRY_SCHEDULED → READY      (transient error, attempts left)
RUNNING → PARKED            (attempts exhausted / on_failure=park): opens human_task(kind=outage|exception)
RUNNING → FAILED            (on_failure=fail, or permanent error with no park)
any non-terminal → PAUSED   (kill switch); PAUSED → previous status on release
PENDING → SKIPPED           (when=false)
WAITING_HUMAN → READY | SUCCEEDED | FAILED  (by task decision)
```

A run completes when every step is terminal (`SUCCEEDED`/`SKIPPED`) and fails if any step is `FAILED` with no compensation.

### 11.4 Error taxonomy

Connectors and skills raise one of: `TransientError` (retry), `PermanentError` (no retry → park or fail), `ConflictError` (data mismatch → `human_task(conflict)`), `LowConfidence` (→ router escalation, then `human_task(low_confidence)`), `PolicyDenied` (→ FAILED with audit). Unknown exceptions are treated as `TransientError` up to `max_attempts`, then park.

### 11.5 Exactly-once side effects

Mandatory for every connector op with `side_effect: true`:
1. `key = sha256(f"{run_id}:{step_id}:{operation}")`. It is deliberately **not** attempt-scoped.
2. `INSERT INTO idempotency_ledger ... ON CONFLICT (key) DO NOTHING RETURNING`. If the row already exists: `succeeded` → return the stored response; `pending` → call `connector.reconcile(key, request)`. If reconcile finds the resource, mark it succeeded. If not, proceed to call.
3. Call the external op with `Idempotency-Key: key` and our `client_reference = key` embedded in the payload.
4. Persist the response and external id in the same DB transaction that marks the step `SUCCEEDED`.

**Chaos test (CI + live demo):** `SIGKILL` the worker after the mock returns 200 but before step 4. Restart. Assert `mock.laptops.count(employee) == 1` and that the step is `SUCCEEDED`.

### 11.6 SLAs and chasing

`sla.fulfil_within`, `remind_at[]` and `escalate_to` create `timers`. Reminders go to the owning team through the notify connector, using a templated message (no LLM). Escalation creates a `human_task(kind=exception)` for the escalation role. Every reminder increments `run.context.sla.reminders` and is audited. This is the feature that replaces "HR chases each one".

## 12. Policy layer

### 12.1 Contract

```python
class Actor(BaseModel):
    type: Literal["human", "agent", "system"]
    id: str
    roles: list[str]

class ActionRequest(BaseModel):
    action: str                    # e.g. "assets.laptop.allocate"
    actor: Actor
    on_behalf_of: str | None       # user id / role pool the actor acts for
    subject: SubjectRef            # e.g. employee:<uuid>
    resource: dict                 # step inputs (masked where sensitive)
    attributes: dict               # computed facts: amount_inr, grade, confidence, etc.
    run_id: UUID | None
    step_id: str | None

class Decision(BaseModel):
    effect: Literal["ALLOW", "REQUIRE_HUMAN", "DENY"]
    approver_role: str | None
    rule_ids: list[str]
    reason: str
    policy_version: str            # composite: pack@version for each pack consulted

def authorize(req: ActionRequest) -> Decision: ...
```

### 12.2 Evaluation order (deny overrides)

1. **Kill switch.** Any active switch matching global, workflow, step, skill or connector → `DENY` with reason `kill_switch` (the engine converts this to `PAUSED`, not `FAILED`).
2. **RBAC.** `actor` permissions (for agents: `principal ∩ skill.allowed_actions`) must include `action` → else `DENY`.
3. **Baseline reserved floor** (`baseline.py`), then `reserved.yaml` additions. If the action is reserved and the actor is not a human holding `approver_role` → `REQUIRE_HUMAN(approver_role)`. If the actor *is* such a human → continue. For `route_only` actions (POSH, grievance, whistleblower): agents may only `route`; any `decide` action → `DENY`.
4. **Approval thresholds** (`approvals.yaml`), e.g. `amount_inr > 150000` on `assets.laptop.allocate` → `REQUIRE_HUMAN(approver_it_spend)`.
5. **Confidence gate.** For skill outputs: `attributes.confidence < skill.threshold` → `REQUIRE_HUMAN(hr_ops)`.
6. **Statutory and policy constraints** (`labour_rules.yaml` assertions) → `DENY` with the cited clause.
7. Otherwise `ALLOW`.

Every call writes a `decisions` row and an `audit_events` row.

### 12.3 Policy pack format

```yaml
pack: approvals
version: 3
owner: hr_admin
rules:
  - id: laptop_high_spend
    description: Laptops above ₹1.5L need IT spend approval
    applies_to: ["assets.laptop.allocate"]
    when: "attributes.amount_inr > 150000"
    effect: REQUIRE_HUMAN
    approver_role: approver_it_spend
    source: "TBO IT Asset Policy §2.3"
```

`reserved.yaml`:
```yaml
pack: reserved
version: 1
reserved:            # additions on top of the immutable baseline
  - action: employment.probation.confirm
    approver_role: manager
  - action: assets.laptop.allocate.exception
    approver_role: approver_it_spend
route_only:
  - "grievance.*"
  - "posh.*"
  - "whistleblower.*"
```

**Baseline (code, immutable):** `hiring.offer.approve`, `hiring.final_decision`, `hiring.candidate.reject`, `hiring.candidate.shortlist_decision` (fair hiring: no automated rejection without human review), `comp.*`, `employment.resignation.accept`, `employment.release_date.agree`, `employment.notice_buyout.agree`, `employment.terminate`, `employment.disciplinary.*`, `performance.pip.*`, `employment.grade_change`, `employment.role_change`, `policy.exception.*`, `statutory.filing.signoff`, `payroll.run.signoff`, `payroll.fnf.signoff`, plus `route_only` for `posh.*`, `grievance.*`, `whistleblower.*`.

### 12.4 Policy lifecycle

Edit in the console (YAML editor with schema validation) or by PR → `relay policy test <pack>` runs every case in `definitions/policies/tests/` → only if green can `hr_admin` **activate**. Activation writes a `policy_versions` row and an audit event. It applies to the *next* `authorize()` call with no deploy and no restart (the loader watches `policy_versions`). Rollback = activate a previous version.

Policy test format:
```yaml
pack: approvals
cases:
  - name: cheap laptop is allowed
    request: { action: assets.laptop.allocate, actor: {type: system, id: engine, roles: [system]}, attributes: { amount_inr: 90000 } }
    expect: { effect: ALLOW }
  - name: expensive laptop needs approval
    request: { action: assets.laptop.allocate, actor: {type: system, id: engine, roles: [system]}, attributes: { amount_inr: 210000 } }
    expect: { effect: REQUIRE_HUMAN, approver_role: approver_it_spend }
```

### 12.5 Labour and policy rules

`labour_rules.yaml` holds decision tables (notice period by grade and tenure, probation length, leave entitlement and accrual start, leave during probation and notice, F&F timeline, PF eligibility, **gratuity eligibility** (continuous service ≥ 5 years, and the statutory formula `last_drawn_basic_da × 15/26 × completed_years` with the statutory cap) computed as an F&F *input* only, never paid without `payroll.fnf.signoff`). **All values are sourced from TBO's sample policies**, and each row carries a `source` field citing the clause. Registered rule functions in `relay/rules/labour.py` read these tables. Property-based tests (hypothesis) cover the date math (weekends, month ends, leap years, holiday calendar).

### 12.6 Kill switch

Console toggle or `relay kill --scope workflow --target onboarding`. This writes `kill_switches`, `NOTIFY`s the workers, and every step boundary checks the in-memory cache (refreshed on notify, max staleness 1s). Running steps finish their current external call (never interrupted mid side effect), then pause.

## 13. Skills (agents) and the model router

### 13.1 Skill manifest

```yaml
skill: document_extractor
version: 2
description: Extract and validate identity and bank fields from uploaded documents
principal: run.subject            # acts on behalf of the data subject's onboarding run
allowed_actions: [documents.extract]
input_schema: { $ref: schemas/document_extractor.in.json }
output_schema: { $ref: schemas/document_extractor.out.json }
pipeline:
  - local_ocr                      # PaddleOCR
  - deterministic_parse            # regex + layout heuristics
  - validators                     # PAN format & name, Aadhaar Verhoeff, IFSC format, DOB sanity
  - model_fallback:                # only if validators fail
      tier: tier1
      escalate_to: tier2
      threshold: 0.82              # from evals/threshold_report.md
      prompt: prompts/document_extractor/v2.md
      pii: mask                    # gateway masks before send
evals: evals/document_extractor/
```

### 13.2 The five V1 skills

| Skill | Purpose | Default path | Model use | Output contract |
|---|---|---|---|---|
| `intake` | Slack/web message → intent + slots → start workflow or answer | Keyword/regex intent table for top intents | tier1 classification with JSON schema; tier2 below threshold | `{intent, slots, confidence}` |
| `document_extractor` | Fields from documents | Local OCR + parsers + validators | tier1 only on masked OCR text when validators fail | `{fields, per_field_confidence, validation}` |
| `exception_analyst` | Builds the human-task context packet for conflicts and failures | Template with facts filled from rules | tier1 writes a 3-line summary + suggested option | `{summary, likely_cause, suggested_option, evidence_refs}` |
| `comms_drafter` | Welcome notes, manager briefs, KT plans | Templates | tier1 personalisation, capped at 150 tokens out | `{subject, body}` |
| `helpdesk` | Policy Q&A with citations | Hybrid retrieval: Postgres FTS + pgvector over policy chunks | tier1 answer grounded only on retrieved chunks. If there are no chunks above the similarity floor, it answers "I'll route this to HR" | `{answer, citations[], confidence}` |

Skills **never** call connectors directly. They return outputs, and the engine decides the next step.

### 13.3 Router

```yaml
# config/models.yaml
providers:
  primary: { litellm_model: "<provider>/<small-model>", price_inr: { in_per_1k: 0.0, out_per_1k: 0.0 } }   # fill from eval
  large:   { litellm_model: "<provider>/<large-model>", price_inr: { in_per_1k: 0.0, out_per_1k: 0.0 } }
tiers:
  tier1: primary
  tier2: large
defaults:
  max_output_tokens: 400
  temperature: 0
  json_mode: true
```

Routing algorithm per call:
1. Build the request from the prompt template + masked inputs (PII Gateway).
2. Call `tier1` with JSON schema output. Invalid JSON → one repair retry on tier1 → else treat as low confidence.
3. Compute confidence:
   - classification: the max class probability from logprobs, where the provider supports it; otherwise self-consistency over 3 samples at temperature 0.7 (only for the intake skill and only when the deterministic intent table missed)
   - extraction: the fraction of fields passing validators, weighted
   - generation (comms): no gate; templates bound the output
4. If `confidence < threshold[skill]` → call `tier2` once. If still below threshold → `LowConfidence` → human task.
5. Emit a `cost_event` per call. Log the masked payload to `model_payloads`.

### 13.4 Threshold justification (required artefact)

`evals/run.py --skill intake` runs the labelled set (≥150 synthetic utterances, ≥100 document samples, ≥60 helpdesk questions). For each threshold t in [0.5, 0.99] it computes accuracy-when-accepted, escalation rate, and expected cost `E(t) = P(accept)·P(error|accept)·C_error + P(escalate_to_tier2)·C_tier2 + P(human)·C_human`, with `C_error` and `C_human` set in `config/app.yaml` (assumption: C_human = 5 min of HR time, valued in INR). It picks `t* = argmin E(t)` subject to `P(error|accept) ≤ 2%`, and writes `evals/threshold_report.md` with the curve as PNG. That chart goes in the Round 1 doc.

## 14. PII gateway and DPDP compliance

### 14.1 Gateway

- Presidio analyzer + custom recognizers: PAN `[A-Z]{5}[0-9]{4}[A-Z]`, Aadhaar 12 digits with Verhoeff checksum, IFSC `[A-Z]{4}0[A-Z0-9]{6}`, UAN 12 digits, Indian mobile numbers, email, person names (NER), addresses, DOB.
- Replace with stable tokens per run: `<PERSON_1>`, `<PAN_1>`. The mapping is kept in process memory only for the duration of the call, never persisted.
- Raw documents and raw images **never** leave our infrastructure (OCR is local). Only masked text goes out.
- Every outbound and inbound payload is stored masked in `model_payloads`, linked from the audit event. In the console, the "What left our system" view shows the exact bytes.
- `docs/pii-inventory.md` lists each skill, the fields it sees, masking applied, provider, hosting region and retention. It is generated by `relay docs pii-inventory` from the skill manifests, so it can't drift.

### 14.2 DPDP features

| Requirement | Implementation |
|---|---|
| Notice & consent | Consent notice versioned in `definitions/notices/`. `consents` row per purpose (`onboarding_verification`, `payroll_setup`, `statutory_registration`, `it_provisioning`). A step whose action maps to a purpose checks consent via a policy rule |
| Purpose limitation | `rbac.yaml` maps actions → purposes. `authorize()` denies if the purpose lacks consent or a legitimate-use basis flag |
| Retention | `retention.yaml` per document category and subject state. A nightly `retention_sweeper` workflow (itself a workflow definition) purges document blobs and redacts extracted fields, and audits each purge |
| Access & correction | `data_request` workflow: the employee asks in Slack → identity check → export package (access) or a correction task to `hr_ops` (correction, human-approved) |
| Security | App-level encryption for 🔒 columns, TLS in transit, least-privilege DB roles, secrets from env only |

## 15. Connectors

### 15.1 Interface

```python
class Connector(Protocol):
    name: str
    def ops(self) -> dict[str, OpSpec]: ...          # declared in definitions/connectors/*.yaml
    async def call(self, op: str, payload: dict, *, idempotency_key: str) -> ConnectorResult: ...
    async def reconcile(self, op: str, idempotency_key: str, payload: dict) -> ConnectorResult | None: ...
    async def health(self) -> HealthStatus: ...
```

`OpSpec`: `side_effect: bool`, `idempotency_mode: header|reference_lookup|natural_key`, `timeout`, `error_map` (HTTP status → error taxonomy), `cost_inr` (if any).

### 15.2 V1 connectors (mapped to the TBO mocks; exact endpoints to be filled once TBO publishes the mock spec)

| Connector | Ops | Mock |
|---|---|---|
| `directory` | `create_account`, `disable_account`, `lookup` | TBO email/directory |
| `assets` | `allocate_laptop`, `recover_laptop`, `lookup_by_reference` | TBO asset |
| `access` | `issue_card`, `revoke_card`, `lookup` | TBO access cards |
| `payroll` | `setup_employee`, `notify_exit`, `lookup` | TBO payroll setup |
| `ticketing` | `create_ticket`, `get_ticket`, `comment` | TBO ticketing (used for fulfilment handoffs and SLA tracking) |
| `statutory` | `register_pf`, `lookup` | **our mock extension** |
| `calendar` | `create_events` | **our mock extension** |
| `notify` | `slack_dm`, `email` | Slack API / mock SMTP |

**Mock extensions and fault injection** live in `mocks/`: `X-Fault: 503|timeout|slow|duplicate_on_retry`, controllable from the demo console to show outage handling live. The judging demo must run on TBO's interfaces. Our extensions only *add* endpoints and never replace TBO's.

## 16. Interfaces

### 16.1 REST API (FastAPI, `/api/v1`, OpenAPI published)

| Method & path | Purpose | Roles |
|---|---|---|
| `POST /events` | Ingest a domain event (e.g. `candidate.offer_accepted`), which starts the matching workflows | system, hr_ops |
| `GET /runs`, `GET /runs/{id}` | List and detail (steps, timeline, decisions) | hr_ops+, manager (own team) |
| `POST /runs/{id}/cancel` | Cancel a run | hr_admin |
| `GET /tasks?mine=true` / `POST /tasks/{id}/claim` / `POST /tasks/{id}/decide` | Human queue | assignee role |
| `GET /policies/{pack}` / `PUT /policies/{pack}/draft` / `POST /policies/{pack}/test` / `POST /policies/{pack}/activate` | Policy lifecycle | hr_admin |
| `GET/POST /kill-switches` | Kill switch | hr_admin |
| `GET /audit?filters` / `GET /audit/{id}` / `GET /audit/verify` | Audit explorer | auditor, hr_admin |
| `GET /metrics/summary?from&to&workflow` | Dashboard metrics | hr_ops+ |
| `GET /definitions` / `POST /definitions/{name}/activate` | Registry | developer, hr_admin |
| `POST /chat` | Web chat entrypoint (same intake path as Slack) | any authenticated |
| `POST /documents` | Joiner upload (signed one-time link) | new_joiner |
| `POST /data-requests` | DPDP requests | employee |

Authorization: every endpoint calls `authorize()` with `actor=human:<user>`. Demo auth uses a JWT from `/auth/demo-login` with seeded users (README lists them).

### 16.2 Slack app

- **DM intents:** "what's my onboarding status", "I've uploaded my documents", "how many leaves do I get in probation", "I want to resign" (V2), "show me my data" (DPDP).
- **Approval cards** (Block Kit): title, the 3-line `exception_analyst` summary, key facts, the policy rule that triggered it, buttons from `task.options`, and a reason modal (required for reject/override). Decisions post to `/tasks/{id}/decide` with the Slack user mapped to a Relay user.
- **Manager feed:** a daily digest of the team's onboarding progress (templated).
- **Team channels:** `#it-fulfilment`, `#admin-fulfilment` receive SLA reminders and escalations.

### 16.3 Web console screens

1. **Home / Dashboard:** automation rate, straight-through rate, escalation rate, cycle time p50/p90, cost per workflow, monthly projection, runs by status. All are clickable down to the underlying runs.
2. **Runs:** a table with filters, then a **Run detail** page with the DAG view (step nodes coloured by status), a timeline of audit events, cost per step, and a "replay" scrubber.
3. **Inbox:** human tasks grouped by kind, with SLA countdowns and the context packet.
4. **Audit explorer:** filter by actor, action, run or effect. Event detail shows inputs hash, policy version, rule ids, model, prompt version, reasoning and "what left our system".
5. **Policies:** pack list → YAML editor with a diff against the active version → "Run tests" → "Activate" (hr_admin), plus version history.
6. **Controls:** kill switches by scope, connector health, fault injection (demo mode only).
7. **Employees:** record view with lifecycle events, documents (redacted), consents and data requests.
8. **Registry:** workflows, skills, connectors and versions, with activation.

UX requirements: empty, loading and error states on every screen, keyboard navigable, and visible role badges. No design sprint: shadcn defaults plus one accent colour.

## 17. Observability, metrics and cost accounting

- One OpenTelemetry trace per run (`trace_id` on `workflow_runs`), spans per step, connector call and model call.
- Structured logs never contain 🔒 fields. A log filter enforces this, and a unit test greps for PII patterns in captured logs.
- **Cost per completed run:**
  `cost_run = Σ llm(tokens_in·p_in + tokens_out·p_out) + Σ cpu_seconds·p_cpu + infra_alloc`
  `infra_alloc = monthly_fixed_inr / projected_monthly_runs_all_workflows`
  where `monthly_fixed_inr` (from `config/infra_costs.yaml`) = VM(s) + managed Postgres + storage + egress + any GPU. `p_cpu` = VM monthly cost ÷ (vCPU-seconds per month).
- **Monthly projection** = Σ_workflow(volume_w × variable_cost_w) + monthly_fixed_inr, using the TBO-provided headcount and transaction volumes (in `config/volumes.yaml`).
- The dashboard shows **measured** values from `cost_events` over real runs on the synthetic dataset, and labels the projection as a projection.

## 18. Security

- Least privilege: separate DB roles for `api`, `worker` and `readonly_auditor`. App roles cannot alter audit tables.
- Secrets only from env / `.env` (gitignored), with a sample `.env.example`. A CI secret scan (gitleaks, MIT) runs on every push.
- Input validation via Pydantic on every endpoint. Uploads: max 10 MB, MIME sniffing, PDF/JPG/PNG only, stored outside the web root.
- Slack request signature verification. Signed one-time upload links (HMAC, 72h expiry).
- Agent tokens: short-lived, scoped to `{run_id, principal, allowed_actions}`.
- OWASP basics: CORS locked to the console origin, rate limits on `/chat` and `/auth`.

## 19. Canonical onboarding workflow (abridged; full file in `definitions/workflows/onboarding.v1.yaml`)

```yaml
name: onboarding
version: 1
subject: employee
trigger: { event: candidate.offer_accepted, require_decision: hiring.offer.approve }
steps:
  - { id: create_prehire, kind: rule, uses: rules.hr.create_prehire, action: employee.create }
  - { id: compute_terms, kind: rule, uses: rules.labour.compute_onboarding_terms, action: hr.terms.compute, needs: [create_prehire] }
  - id: request_documents_and_consent
    kind: connector
    uses: notify.slack_dm
    action: documents.request
    needs: [compute_terms]
    inputs: { template: "onboarding_docs_request", consent_notice: "dpdp_onboarding_v1" }
  - id: await_documents
    kind: wait
    for_subject_action: documents.uploaded
    needs: [request_documents_and_consent]
    sla: { remind_at: [24h, 48h], escalate_after: 72h, escalate_to: hr_ops }
  - { id: extract_documents, kind: skill, uses: document_extractor, action: documents.extract, needs: [await_documents] }
  - { id: cross_check, kind: rule, uses: rules.hr.cross_check_identity, action: employee.verify, needs: [extract_documents] }
  - { id: activate_employee_id, kind: rule, uses: rules.hr.allocate_employee_code, action: employee.activate_code, needs: [cross_check] }
  - parallel:
      id: provision
      needs: [activate_employee_id]
      branches:
        - [{ id: provision_identity, kind: connector, uses: directory.create_account, action: directory.account.create }]
        - [{ id: select_laptop_spec, kind: rule, uses: rules.it.select_laptop, action: assets.laptop.select },
           { id: order_laptop, kind: connector, uses: assets.allocate_laptop, action: assets.laptop.allocate, needs: [select_laptop_spec], sla: { fulfil_within: 24h, remind_at: [12h], escalate_to: it_ops } }]
        - [{ id: issue_access_card, kind: connector, uses: access.issue_card, action: access.card.issue }]
        - [{ id: setup_payroll, kind: connector, uses: payroll.setup_employee, action: payroll.employee.setup }]
        - [{ id: register_statutory, kind: connector, uses: statutory.register_pf, action: statutory.pf.register }]
  - { id: assign_buddy, kind: rule, uses: rules.hr.assign_buddy, action: employee.buddy.assign, needs: [activate_employee_id] }
  - { id: notify_manager, kind: skill, uses: comms_drafter, action: comms.manager_brief, needs: [assign_buddy] }
  - { id: schedule_day_one, kind: connector, uses: calendar.create_events, action: calendar.events.create, needs: [provision] }
  - { id: policy_acknowledgements, kind: wait, for_subject_action: policies.acknowledged, needs: [schedule_day_one] }
  - { id: readiness_gate, kind: rule, uses: rules.hr.readiness_gate, action: employee.activate, needs: [provision, policy_acknowledgements] }
  - { id: schedule_probation_review, kind: wait, until: "ctx.terms.probation_end", needs: [readiness_gate] }
  - id: probation_confirmation
    kind: human
    action: employment.probation.confirm
    needs: [schedule_probation_review]
    task: { assignee_role: manager, assignee_user: "ctx.employee.manager_user_id", options: [confirm, extend, refer_to_hr] }
```

Note: the run is reported as **completed for metrics** at `readiness_gate` (the onboarding outcome), and the probation branch continues as a linked child run (`onboarding.probation`), so cycle time isn't distorted by a 6-month timer.

## 20. Developer extension guide (summary of `docs/developer-guide.md`)

| I want to add… | I touch | Command |
|---|---|---|
| A workflow | `definitions/workflows/<name>.v1.yaml` + optional rule functions in `relay/rules/` | `relay new workflow <name>` → scaffolds YAML, a policy test stub and a simulation test |
| A skill / agent | `definitions/skills/<name>.yaml`, `definitions/prompts/<name>/v1.md`, `relay/skills/<name>.py` (implements `Skill.run`), `evals/<name>/` | `relay new skill <name>` |
| An integration | `definitions/connectors/<name>.yaml`, `relay/connectors/<name>.py` implementing `call`/`reconcile`/`health` | `relay new connector <name>` |
| A policy | `definitions/policies/<pack>.yaml` + `tests/<pack>.yaml` | `relay policy test <pack>` then activate in console |

**Always:** `relay validate` (schemas + references + cycles + reserved checks) and `relay simulate <workflow> --scenario <seed>` (fake clock, mocked connectors, fault injection) must pass before activation.

**Authoring accelerator:** `relay draft workflow "<plain-English requirement>"` uses tier2 to propose YAML + tests from the registry catalogue. The developer reviews, `relay validate` gates it, and it is only ever activated by a human. This exists to win the live extension test, and its output is ordinary governed config.

## 21. Testing strategy

| Layer | What | Gate |
|---|---|---|
| Unit | Rules, CEL evaluator, ledger, router, PII recognizers | ≥85% line coverage on `relay/core` and `relay/rules` |
| Policy | All `definitions/policies/tests/*.yaml`; property test: *no agent or system actor ever receives ALLOW on any baseline-reserved action* (hypothesis over random requests) | Must pass to activate |
| Architecture | import-linter contracts (only connectors import httpx; only router imports litellm; `core` imports nothing from `skills`/`connectors` concrete modules) | CI |
| Simulation | Every workflow over every seed scenario with a fake clock; asserts the final states and metrics | CI |
| Chaos | Worker SIGKILL at each side-effect boundary; mock 503 storms; duplicate deliveries; asserts exactly-once | CI nightly + live demo |
| Evals | Skill accuracy, threshold report, regression vs the last committed report | CI on prompt/skill change |
| E2E smoke | Playwright: login → dashboard → resolve conflict task → policy activate → kill switch | CI |
| Audit integrity | `relay audit verify` after every simulation | CI |

## 22. Seed scenarios and demo script

**Seed (from the TBO synthetic dataset + scenario overlays):** 25 clean hires across grades; 3 PAN-name mismatches; 2 joining-date conflicts (offer vs record); 1 blurry Aadhaar (low confidence → tier2 → human); 2 high-spec laptops (approval threshold); 1 asset-API outage window; 2 joiners who ignore the document request (SLA chase → escalation); 5 exits for V2.

**5-minute demo:**
1. (0:00) Dashboard: automation rate, cost per onboarding in INR, cycle time, all from real runs.
2. (0:40) Trigger a new hire. The run DAG fills live: IT, Admin, Payroll and PF complete on their own, and Slack shows the joiner flow.
3. (1:40) **Chaos:** kill the worker mid laptop order, restart → exactly one laptop. Show the ledger row.
4. (2:20) A conflict lands in HR's Slack as a one-click card with the analyst summary. Resolve it, and the run continues.
5. (3:00) **Governance:** edit `approvals.yaml` live (lower the laptop threshold), run tests, activate. The next run routes to an approver. Then press the kill switch: all runs pause, and release resumes them.
6. (3:50) **Explainability:** click any agent action → policy version, rule id, model, prompt version, and the exact masked payload that left the system.
7. (4:30) **Extensibility:** show the Exit PR: YAML + policies only, zero core diff, already running.

## 23. Delivery plan and work packages (for coding agents)

Dependencies are in brackets. Each package ships with tests and updates `docs/`.

| WP | Scope | Depends on | Acceptance criteria |
|---|---|---|---|
| WP1 Foundations | Repo skeleton, uv workspace, Compose, config loader, DB models + Alembic migrations for Section 10, encryption helpers, seed loader for the TBO dataset | none | `docker compose up` brings up a healthy stack; migrations apply cleanly; seed loads; audit triggers reject UPDATE/DELETE |
| WP2 Audit & cost core | Hash-chained audit writer + verifier, cost_event writer, metric queries (Section 3.3) | WP1 | `relay audit verify` passes; tampering a row is detected; metric SQL unit-tested against fixtures |
| WP3 Policy engine | `authorize()`, CEL evaluator, baseline floor, pack loader + hot reload, kill switches, policy test runner, CLI `relay policy test` | WP1, WP2 | All Section 12 examples pass; property test for the reserved floor passes 10k cases; activation applies within 1s without restart |
| WP4 Workflow engine | DSL schemas, registry validation, interpreter, leasing, timers, retries, state machine, parallel, wait, human steps, SLA timers, ledger + exactly-once | WP1–WP3 | Simulation of a toy workflow; chaos test passes 100/100 runs with 2 workers; `relay validate` rejects the invalid fixtures |
| WP5 Connectors & mocks | Connector base + V1 connectors, mock extensions, fault injection, health | WP4 | Each op has a contract test incl. reconcile; a 503 storm leads to park → outage task |
| WP6 Models, PII, skills | LiteLLM router, PII gateway + India recognizers, model_payloads logging, five skills, local OCR, retrieval index over policies, eval harness + threshold report | WP3, WP4 | Recognizer tests (precision ≥0.95 on fixture set); no raw PII in any `model_payloads` row (test); threshold report generated |
| WP7 Onboarding vertical | `onboarding.v1.yaml`, rule functions, labour tables from TBO policies, policy packs, scenario simulations | WP4–WP6 | All seed scenarios produce the expected end states; step automation rate ≥85% on the full seed |
| WP8 API | Routers in Section 16.1, demo auth, RBAC via authorize | WP3, WP4 | OpenAPI generated; endpoint auth tests (each role allowed/denied matrix) |
| WP9 Slack | Bolt app, intents via intake skill, approval cards, digests, upload links | WP6, WP8 | Demo journeys 2 and 4 run end-to-end in Slack |
| WP10 Web console | Screens in Section 16.3 | WP8 | Playwright smoke passes; every screen has empty, loading and error states |
| WP11 Exit vertical (extension proof) | `exit.v1.yaml`, policies, connector ops reuse | WP7 | CI check: zero diff under `relay/core/`; Exit scenarios pass |
| WP12 Docs & submission | README (setup, demo users, objective checklist, licences, AI tools used), developer guide, PII inventory, architecture doc, threshold report, cost projection | all | A fresh clone to a running demo in ≤10 minutes following the README |

**Sequencing:** WP1 → (WP2, WP3 in parallel) → WP4 → (WP5, WP6, WP8 in parallel) → WP7 → (WP9, WP10 in parallel) → WP11 → WP12. Build the onboarding vertical **without any LLM first** (skills stubbed by templates/validators), then switch the skills on. The system must already work with all AI disabled.

**Coding conventions for agents:** typed everything (mypy strict); Pydantic models at every boundary; no business logic in API routers; no `print`; no network calls in tests except to local mocks; one migration per WP; conventional commits (`feat(engine): ...`), one logical change per commit (finalists are questioned on commit history); every new public function needs a docstring and a test.

## 24. Risks and mitigations

| Risk | Mitigation |
|---|---|
| TBO mock APIs differ from assumptions | Connectors isolate the shape; adapt only `definitions/connectors/*.yaml` + the connector module |
| The in-house engine has subtle concurrency bugs | Chaos suite from day one; DBOS fallback ADR ready; keep the engine small |
| Live extension test runs over time | Rehearse it 5+ times with someone who didn't write the core; scaffolds + `relay draft` |
| Cost numbers look tiny and therefore implausible | Include fixed infra allocation honestly; show the formula and inputs |
| LLM provider outage during the demo | Router fallback to a second provider; the system works with AI disabled (templates + human tasks) |
| Over-building UI | UX is 10%: shadcn defaults; spend effort on the engine, policy and audit |

## 25. Open questions (resolve when TBO publishes details)

0. **TBO starter kit** (synthetic dataset, mock APIs, sample policies) has not been received yet. Until it arrives, build on our own fixtures in `seed/fixtures/` and `mocks/` (owned by WP1/WP5). When it arrives, drop it in `seed/tbo_starter_kit/` and adapt only the loaders and connector manifests.
1. Exact mock API specs (endpoints, auth, idempotency support).
2. TBO headcount and monthly transaction volumes for the projection.
3. Team size and round dates → final WP allocation.
4. Whether judges expect Teams specifically, or Slack is acceptable (we assume Slack + a documented Teams adapter).
5. Hosting constraints for the demo (local vs cloud; any region requirement for model hosting).

## 26. Licences (to be listed in the README)

FastAPI (MIT), Pydantic (MIT), SQLAlchemy (MIT), Alembic (MIT), PostgreSQL (PostgreSQL), pgvector (PostgreSQL), cel-python (Apache-2.0), LiteLLM (MIT), PaddleOCR (Apache-2.0), Presidio (MIT), sentence-transformers (Apache-2.0), bge-small-en-v1.5 (MIT), slack-bolt (MIT), Typer (MIT), Next.js (MIT), Tailwind (MIT), shadcn/ui (MIT), TanStack Query (MIT), Recharts (MIT), OpenTelemetry (Apache-2.0), pytest (MIT), hypothesis (MPL-2.0), import-linter (BSD-2), Playwright (Apache-2.0), gitleaks (MIT), uv (MIT/Apache-2.0). Verify each licence at the pinned version before submission. Model licences are chosen at eval time and must permit commercial use.

## 27. Hackathon execution plan (v1.1)

### 27.1 Two sessions

Two Claude Code sessions build in parallel, each driven by one team member. They coordinate only through the repo: `CONVERSATION.md` (messages, polls, hand-offs), GitHub Issues (work items) and PRs (code). Protocol: `docs/collab/PROTOCOL.md`.

| Session | Driver | Role | Owns |
|---|---|---|---|
| **A** | Devansh | Lead: creates and assigns issues, final call on disputes, keeps `docs/collab/STATUS.md` pinned state | WP1, WP3, WP4, WP7, WP9, WP11 (the engine and governance spine) |
| **B** | Sarthak (`sarthakviche`) | Builder: picks up `session:B` issues, can open issues and polls | WP2, WP5, WP6, WP8, WP10 (audit/cost, integrations, AI, API, console) |
| Both | | | WP12 docs; UI and feature enhancements once P0+P1 are done |

The split follows the Section 23 dependency graph so neither session waits long: A lands a thin WP1 skeleton first (DB models, config loader, Compose), then B starts WP2 and WP5 against it while A builds WP3 → WP4.

### 27.2 Priorities

| Priority | Meaning | Contents |
|---|---|---|
| **P0** | Demo-critical vertical slice | Onboarding run end to end with AI disabled; authorize() with baseline floor + approvals pack; hash-chained audit; idempotency ledger + chaos test; mocks with fault injection; human queue; metrics + cost dashboard; web console (Dashboard, Runs, Run detail DAG, Inbox, Policies, Controls) |
| **P1** | Scored depth | Model router + PII gateway + 5 skills on a real provider; threshold report from evals; DPDP consent ledger + data requests; Exit vertical with zero core diff; Slack app; audit explorer with "what left our system" |
| **P2** | Polish | `relay draft workflow`, run replay scrubber, Presidio/PaddleOCR upgrades, Teams adapter doc, UI animations |

### 27.3 Timeline

Round dates are still to be confirmed by TBO; update this table when they are.

| When | Session A | Session B | Exit criterion |
|---|---|---|---|
| Night 1 (7 to 8 Nov) | WP1 skeleton → WP3 policy engine → WP4 engine core | WP2 audit & cost → WP5 mocks + connectors → WP6 PII regex + router (mock provider) | A toy workflow runs in simulation; mocks answer with fault injection |
| Day 2 | WP4 chaos test → WP7 onboarding vertical | WP8 API → WP10 console (P0 screens) | Onboarding runs end to end from the console with AI disabled; dashboard shows real numbers |
| Day 3 | WP11 Exit vertical, WP9 Slack | WP6 real skills + evals + threshold report, WP10 remaining screens | P1 complete; Exit PR shows zero `relay/core/` diff |
| Day 4+ | WP12 docs, demo rehearsal, extension-test rehearsals | UI polish, enhancements | Fresh clone to running demo in ≤ 10 minutes |

### 27.4 Offline-first and fallbacks

- `config/models.yaml` ships a `mock` provider: deterministic, schema-valid outputs keyed on a hash of the masked prompt, with token counts and a nominal price so cost accounting still exercises every path. CI always uses it.
- PII: regex + checksum recognizers (PAN, Aadhaar/Verhoeff, IFSC, UAN, mobile, email) are P0. Presidio NER for names and addresses is a P1 upgrade behind the same interface.
- OCR: seed documents are generated as text-layer PDFs/images with a JSON sidecar. `ocr.engine: sidecar | paddle` in `config/app.yaml`. PaddleOCR is P1.
- Chat: the web chat (`POST /chat`) is P0; Slack (Socket Mode) is P1 and uses the same intake path.

