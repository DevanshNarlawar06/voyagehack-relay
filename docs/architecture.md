# Relay architecture

Relay is a governed workflow runtime for HR. Agents do the routine work across teams from start to
finish. Humans make every decision with legal, financial or human consequences. Every action is
policy-checked, audited and costed in INR. This document explains how it is built and why. The
full specification is [PRD.md](../PRD.md) (sections 6 to 17).

## 1. The shape of the system

```
 Employees, managers ──► Slack app (Socket Mode) ─┐
 Joiners ──────────────► Upload page (signed link) ┼──► FastAPI ──► Engine workers (×2)
 HR, approvers, auditor ► Web console (Next.js) ──┘        │              │
                                                           ▼              ▼
                    PostgreSQL 16: records · runs · steps · tasks · ledger · audit chain · cost
                                                                          │
                     Connectors (HTTP, idempotency ledger) ──► TBO systems (mocks in the demo)
                     Model router ──► PII gateway ──► model provider (offline mock by default)
```

Everything durable lives in one Postgres database: the system of record, the work queue, the
idempotency ledger, the hash-chained audit log, cost events and vectors. One store means one
transaction can update a step, write its audit event and record its cost, so they never disagree.

## 2. Workflows are data, the engine is code

A workflow is a versioned YAML file in `definitions/workflows/`, checked against a JSON schema and
against the policy catalogue before it is stored (`relay validate`). Each step is one of six typed
kinds:

| Kind | What it does | Example |
|---|---|---|
| `rule` | Deterministic Python, registered by name | Notice period from the grade table |
| `connector` | An external side effect through a connector | Allocate a laptop |
| `skill` | Model-backed judgement, with a confidence | Extract fields from a PAN card |
| `human` | A decision by a named person or role | Manager accepts a resignation |
| `wait` | A timer, or something the subject must do | Joiner uploads documents |
| `parallel` | Branches that run at the same time | IT, Admin, Payroll and PF provisioning |

The orchestrator is never a model (PRD 6.1). The engine (`relay/core/engine/`) is an interpreter
over Postgres: a worker leases a ready step with `FOR UPDATE SKIP LOCKED`, runs it, writes the
result and readies the steps whose `needs` are met. A crashed worker's lease expires and another
worker takes the step. Expressions (inputs, outputs, `when`, timers) are CEL, which is safe and not
Turing-complete. Stored workflow versions are immutable: a change is a new version, and running
work keeps the version it started with.

A workflow can start linked child runs when it completes (`on_complete`). Onboarding uses this to
hand the probation review to its own run, so onboarding's cycle time ends at day-one readiness.

## 3. Policy before every action

Every step calls `authorize()` before it runs, and every call leaves a `Decision` row (PRD 6.2).
The policy engine (`relay/core/policy/`) answers `ALLOW`, `REQUIRE_HUMAN` (with the approver role)
or `DENY`, from four policy packs in `definitions/policies/`:

- **rbac**: roles to permitted actions, the action catalogue and DPDP purposes.
- **reserved**: decisions only a named human role may take. A baseline set is compiled into
  `relay/core/policy/baseline.py` (offer approval, resignation acceptance, F&F sign-off,
  compensation and more). Configuration can only add to it. Agents and system actors never get
  `ALLOW` on a reserved action, under any configuration (PRD 6.3).
- **approvals**: thresholds, such as a laptop over a price going to the IT spend approver.
- **labour_rules**: decision tables (probation, notice, leave, gratuity) and assertions, each row
  citing its source clause.

Policy packs are edited, tested and activated from the console with versioning and rollback, and
each pack ships with test cases (`relay policy test`). Rules never live in prompts (PRD 6.8).

**Agents act for a principal.** A skill's effective permissions are the principal's permissions
intersected with the skill's own allowlist (PRD 6.7). The comms drafter, for example, may draft a
manager brief for the run's subject, and can never set up payroll even when the principal could.

## 4. Side effects exactly once

External systems are reached only through connectors (`relay/connectors/`), described by a YAML
manifest per system: operations, paths, idempotency mode and error mapping. Only
`relay.connectors` may import `httpx`, enforced by import-linter (PRD 6.4).

Before a side effect, the engine writes an idempotency-ledger row keyed by run, step and
operation. Systems that accept an idempotency header get one; systems that do not are reconciled
by looking up our client reference before any retry. Errors are classified as transient (retry
with backoff), permanent or conflict (to a person). The chaos test kills workers mid-call 100
times and checks that every laptop is ordered exactly once.

## 5. Humans in the loop, with context

When a step needs a person (a reserved decision, an approval, a conflict, low confidence, an
outage), the engine opens a human task routed to a role, or to a named person when the workflow
says so. The exception analyst skill then writes a three-line summary, the likely cause and a
suggested option into the task, in its own transaction so it never holds the run's locks.

People decide in the console inbox or on a Slack card, through the same code path
(`relay/api/task_decisions.py`). Overriding, rejecting or going against the suggestion needs a
reason. SLA timers remind and then escalate when nobody acts.

## 6. Models behind a router and a PII gateway

Model calls go only through the model router (`relay/models/router.py`, the only importer of
`litellm`, PRD 6.5). The router picks a tier from `config/models.yaml` (tier1 small, tier2 larger),
so a provider change is a config change. Every call passes the PII gateway first: regex
recognizers for Indian identifiers (PAN, Aadhaar, IFSC, UAN) and known names are masked, and only
the masked payload leaves the system and is logged. Each skill's manifest declares what it sees,
and `docs/pii-inventory.md` is generated from those manifests.

Skills report a confidence. Below the threshold set by the evals, the work goes to tier2 or to a
person. Sensitive values (approved CTC, document fields, F&F amounts) travel through a run's
context **sealed** (encrypted) and are opened only inside the connector call or skill that needs
them, so neither the run context nor step outputs hold them in plain text.

## 7. Audit and explainability

The audit log is append-only and hash-chained: each event carries the hash of the one before, and
a database trigger plus revoked privileges stop UPDATE and DELETE (PRD 6.6). `relay audit verify`
and the console check the chain. Every event is stamped with the workflow version, policy version
and, for model work, the skill and prompt version and model id (PRD 6.10), so any action can be
traced to the exact rule, prompt and masked payload behind it.

## 8. Kill switches

HR can stop everything, one workflow, one connector or one skill from the console. Switches are
checked at every step boundary and take effect within a second (PRD 6.9). Releasing a switch
resumes the paused work.

## 9. Cost, measured

Every model call and every completed run writes a cost event. The dashboard shows cost per
completed workflow, automation rate, escalation rate and cycle time from these rows, not from
estimates. See [cost-projection.md](cost-projection.md).

## 10. Proof of extensibility: Exit

The Exit vertical (`definitions/workflows/exit.v1.yaml`) was added with **zero changes under
`relay/core/`**, which a CI job checks on the PR. It needed a workflow file, policy actions and
tests, rule functions in `relay/rules/exit.py`, and a table. It reuses the same engine, policy,
connectors, comms skill, Slack cards and audit. Two reserved decisions stop it for named people:
the manager accepts the resignation, and finance signs off the full and final settlement.

## 11. Interfaces

- **Web console** (`web/`): dashboard, runs with a step DAG and audit timeline, the human task
  inbox, employees, policies (edit, test, activate, roll back), audit explorer, workflow registry,
  chat, and controls for kill switches and fault injection.
- **Slack** (`relay/slack/`): DMs on the web chat's intake path, one-click approval cards, a reason
  modal and a manager digest. See [slack.md](slack.md).
- **Joiner upload page**: a signed one-time link (HMAC, 72 hours) sent by the onboarding run. It
  takes the documents and the DPDP consent.
- **REST API** (`/api/v1`): every route goes through `authorize()`. OpenAPI at `/docs`.

## 12. Quality gates

ruff, mypy strict, import-linter contracts (core never imports connectors, skills, the API or Slack; only connectors
reach HTTP; only the router reaches models), pytest with property-based tests for date math,
simulation tests that play every seed scenario on the real engine, the chaos test, and a live
end-to-end run in CI with the API, two workers and the mocks over real HTTP.
