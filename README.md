# Relay

**AI-native, governed HR services platform for TBO.com** (VoyageHack 4.0, problem statement 6: Darwinbox replacement).

Agents do the routine HR work end to end, humans make every decision with legal, financial or human consequences, and every action is policy-checked, audited and costed in INR.

- Vertical 1 (depth): **Employee Onboarding**
- Vertical 2 (extension proof): **Employee Exit**, built only through extension points

## Documents

| Doc | Purpose |
|---|---|
| [PRD.md](PRD.md) | Full spec (v1.1). Section 27 is the execution plan |
| [docs/problem-statements/PS6-darwinbox-hr.txt](docs/problem-statements/PS6-darwinbox-hr.txt) | Original TBO brief |
| [docs/collab/PROTOCOL.md](docs/collab/PROTOCOL.md) | How the two build sessions coordinate |
| [CONVERSATION.md](CONVERSATION.md) | Session-to-session message log |
| [docs/collab/STATUS.md](docs/collab/STATUS.md) | Live status of each session |
| [docs/developer-guide.md](docs/developer-guide.md) | How to add a workflow, skill, connector or policy |

## Repository layout

```
config/        models, infra costs, app settings, volumes
definitions/   extension surface: workflows, policies (+tests), skills, prompts, connectors, notices
relay/         Python package; relay/core/ is frozen for extensions
web/           Next.js HR console
mocks/         TBO mock APIs + our extensions + fault injection
seed/          synthetic dataset loaders and demo scenarios
evals/         labelled sets, eval runner, threshold report
tests/         unit, policy, simulation, chaos, e2e
docs/          architecture, developer guide, PII inventory, ADRs, collaboration
scripts/       sync.sh, post.sh, status.sh (session coordination)
```

## Quick start

Needs Python 3.12, [uv](https://docs.astral.sh/uv/), Docker. Target: fresh clone to running demo in 10 minutes or less (the full demo arrives with later work packages).

```bash
cp .env.example .env              # then set RELAY_ENCRYPTION_KEY: uv run relay config gen-key
docker compose up -d postgres     # Postgres 16 + pgvector on host port 55432
uv sync                           # install dependencies (incl. dev tools)
uv run relay config check         # validate config/*.yaml
uv run relay db upgrade           # apply migrations
uv run pytest -q                  # tests (DB tests skip if Postgres is not running)
```

Quality gate (same as CI): `uv run ruff check relay tests && uv run ruff format --check relay tests && uv run mypy relay && uv run lint-imports && uv run pytest -q`.

## Team and AI tools

Built by a two-person team, each working with a Claude Code session (Anthropic). Coordination between sessions is logged in `CONVERSATION.md`. Licences of all dependencies are listed in PRD Section 26 and will be verified before submission.
