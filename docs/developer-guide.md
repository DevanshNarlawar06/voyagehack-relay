# Developer guide

Relay is extended by adding files, not by changing `relay/core/`. There are four extension points, each with a command that generates something working. Run its test, then change it.

| I want to add… | Command | I edit |
|---|---|---|
| A workflow | `relay new workflow <name>` | `definitions/workflows/<name>.v1.yaml`, optional rules in `relay/rules/` |
| A skill | `relay new skill <name>` | `definitions/skills/<name>.yaml`, the prompt, `relay/skills/<name>.py` |
| An integration | `relay new connector <name>` | `definitions/connectors/<name>.yaml` |
| A policy | Edit a pack | `definitions/policies/<pack>.yaml` and `tests/<pack>.yaml` |

Before anything is activated:

```bash
uv run relay validate
```

```bash
uv run relay policy test
```

```bash
uv run pytest tests/simulation
```

## Add a workflow

```bash
uv run relay new workflow leave_request
```

This creates the workflow, a policy test and a simulation test, and all three pass as generated. Each step has a kind:

| Kind | Use it for | Example |
|---|---|---|
| `rule` | Anything computable. Statutory values and dates always live here, never in prompts | `uses: rules.labour.compute_onboarding_terms` |
| `connector` | Any external side effect | `uses: assets.allocate_laptop` |
| `skill` | Judgement from a model | `uses: document_extractor` |
| `human` | A decision by a named person | `task: { assignee_role: manager, options: [confirm, extend] }` |
| `wait` | A timer or a person's action | `for_subject_action: documents.uploaded` |

Group steps that can run together in a `parallel` block. Inside a branch, each step depends on the one before it, and `needs: [block_id]` means every step in the block.

**Every `action` must be declared in `definitions/policies/rbac.yaml`, with a role allowed to do it.** `relay validate` reports any that are not. The engine calls `authorize()` for every step, so you never write permission checks yourself.

Three things to know:
- **Consent:** an action with a `purpose` in `rbac.yaml` needs that purpose in `ctx.consented_purposes`, unless the purpose is a legitimate use.
- **Reserved decisions cannot be automated.** Baseline ones are in `relay/core/policy/baseline.py`, and configuration can only add to them. A human step for one must name the approver's role, or the definition will not validate.
- **Outputs bind into context:** `outputs: { terms: "result" }` makes `ctx.terms` available to later steps. Expressions are CEL, never Python.

## Add a skill

```bash
uv run relay new skill policy_summarizer
```

A skill returns an output, and the engine decides what happens next. Skills never call connectors. Three rules:
- **It must work with AI off.** Have a deterministic answer first. The router's mock provider exists so CI and demos run with no key.
- **Rules never live in prompts** (PRD 6.8). Pass in the outputs of rules; never ask a model for a date, an amount or eligibility.
- **Never put raw identifiers in the output.** Step output is stored unencrypted. See `seal` and `open_sealed` in `relay/skills/document_extractor.py`.

Then:
1. Add it to `build_skills` in `relay/skills/__init__.py`.
2. Declare its `allowed_actions` in `rbac.yaml`.
3. Fill in `data_seen`, `masking` and `model_use` in its manifest, and run `relay docs pii-inventory`.
4. Add labelled examples to `evals/<name>/labelled.jsonl`. `evals/run.py` shows how they are scored.

An agent's permissions are always its principal's permissions intersected with `allowed_actions`, so a skill can never do more than the person it acts for.

## Add an integration

```bash
uv run relay new connector crm
```

Usually only the manifest changes: `HttpConnector` handles the calls, error mapping, reconcile and health checks. Choose how a repeat is kept safe:

| `idempotency_mode` | The system… | On a retry |
|---|---|---|
| `header` | honours `Idempotency-Key` | Replays the stored response |
| `reference_lookup` | has no protection | Reconcile looks up `client_reference` first |
| `natural_key` | refuses a second record for a key, with a 409 | Adopts the record if it is ours; otherwise a conflict for a person |

Keep `response_fields` to what workflows read. Responses are stored unencrypted, so never list a sensitive field.

Only `relay.connectors` may import `httpx`, and only `relay.models` may import `litellm`. Import-linter fails CI otherwise. To wire a concrete class into the API or a worker, use `relay/bootstrap.py`.

To test against failures, send `X-Fault: 503|timeout|timeout_after_commit|slow|duplicate_on_retry` to the mock service, or see `mocks/README.md` for standing rules.

## Change a policy

Edit a pack and its tests, then activate in the console (Policies), or:

```bash
uv run relay policy test approvals --draft my_draft.yaml
```

Activation runs every test, and a failure blocks it. A new version applies on the next `authorize()` with no restart. Stored versions never change, so each audit event still points at the rules that applied.

## How to know it worked

- **The console** shows each run's graph, and why each step was allowed.
- **`relay audit verify`** recomputes the hash chain.
- **`tests/chaos`** proves exactly-once under hard kills: `uv run python -m tests.chaos.chaos run --runs 100 --workers 2`.
