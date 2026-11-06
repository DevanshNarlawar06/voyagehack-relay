# CLAUDE.md

Instructions for every Claude Code session working in this repo.

## What this is
**Relay**: an AI-native, governed HR services platform for TBO (VoyageHack 4.0, problem statement 6, "Darwinbox"). The spec is `PRD.md` (v1.1). The original brief is `docs/problem-statements/PS6-darwinbox-hr.txt`.

## Two sessions build this together
- **Session A** = Devansh's session (lead). **Session B** = the teammate's session. Your driver's first prompt tells you which one you are; if it did not, ask.
- You coordinate with the other session **only** through the repo. Read and follow `docs/collab/PROTOCOL.md` before doing anything else.
- Sync: `bash scripts/sync.sh <A|B>`. Message: `bash scripts/post.sh <FROM> <TO|ALL> <TYPE> "body"`. Status: `bash scripts/status.sh <A|B> "..."`.
- Pushing your own feature branches, opening PRs, merging per the protocol's merge rule, and posting to `CONVERSATION.md` / `STATUS.md` are all pre-authorized in this repo. Nothing else goes straight to `main`.
- On Windows, run the scripts with Git Bash (`bash scripts/...`).

## Non-negotiables (PRD Section 6)
1. Step kinds are typed (`rule`, `connector`, `skill`, `human`, `wait`, `parallel`). The orchestrator is never an LLM.
2. Every step calls `authorize()` first; no action without a `Decision` row.
3. Reserved decisions have an immutable baseline floor in code; config can only add. Agents and system actors never get `ALLOW` on reserved actions.
4. External side effects only through connectors with the idempotency ledger. Only `relay.connectors.*` imports `httpx`.
5. Model calls only through the router + PII gateway. Only `relay.models.router` imports `litellm`.
6. Audit log is append-only and hash-chained.
7. Agents act on behalf of a principal: permissions = principal ∩ skill allowlist.
8. Rules never live in prompts.
9. Kill switches checked at every step boundary.
10. Everything versioned is stamped on audit events.

## Conventions
- Python 3.12, `uv`, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, Postgres 16. mypy strict, ruff, pytest.
- Typed everything, Pydantic at every boundary, no business logic in API routers, no `print`, no network in tests except local mocks.
- Conventional commits, one logical change per commit. Every public function has a docstring and a test.
- Synthetic data only. Never commit `.env`, keys, or real personal data.
- Extensions (new workflows, skills, connectors, policies) must not change `relay/core/`.
