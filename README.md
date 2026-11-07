# Relay

**AI-native, governed HR services platform for TBO.com** (VoyageHack 4.0, problem statement 6: Darwinbox replacement).

Agents do the routine HR work end to end, humans make every decision with legal, financial or human consequences, and every action is policy-checked, audited and costed in INR.

- Vertical 1 (depth): **Employee Onboarding**
- Vertical 2 (extension proof): **Employee Exit**, built only through extension points

## Documents

| Doc | Purpose |
|---|---|
| [PRD.md](PRD.md) | Full spec (v1.1). Section 27 is the execution plan |
| [docs/architecture.md](docs/architecture.md) | How Relay is built and why: engine, policy, connectors, models, audit, cost |
| [docs/cost-projection.md](docs/cost-projection.md) | Measured cost per onboarding and the monthly projection formula |
| [docs/developer-guide.md](docs/developer-guide.md) | How to add a workflow, skill, connector or policy |
| [docs/slack.md](docs/slack.md) | The Slack app and how to set it up |
| [docs/pii-inventory.md](docs/pii-inventory.md) | What personal data each skill sees, generated from the manifests |
| [docs/problem-statements/PS6-darwinbox-hr.txt](docs/problem-statements/PS6-darwinbox-hr.txt) | Original TBO brief |
| [docs/collab/PROTOCOL.md](docs/collab/PROTOCOL.md) | How the two build sessions coordinate |
| [CONVERSATION.md](CONVERSATION.md) | Session-to-session message log |
| [docs/collab/STATUS.md](docs/collab/STATUS.md) | Live status of each session |

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

## Quick start: fresh clone to running demo

Needs Docker, Python 3.12, [uv](https://docs.astral.sh/uv/) and Node 20+. Everything runs offline:
the mock TBO systems stand in for the real ones, and skills use the offline `mock` model provider
until a provider key is set (issue #20). Target: 10 minutes or less.

**1. Configure** (one minute)

```bash
cp .env.example .env
uv run relay config gen-key       # paste the output into .env as RELAY_ENCRYPTION_KEY
```

**2. Start the backend** (Postgres, mock TBO systems, the API, two engine workers)

```bash
docker compose up -d --build
```

The API container applies migrations and creates the demo users on start. Check it with
`curl http://localhost:8000/api/v1/health`.

**3. Load the synthetic seed and play the demo scenarios**

```bash
uv sync
uv run relay seed load            # 46 employees, 37 candidates, 5 exits (synthetic only)
uv run relay simulate             # every PRD 22 scenario through onboarding on a fast clock
```

`simulate` prints each candidate's end state (34 completed, 2 waiting on the joiner, 1 refused for an
unapproved offer) and the step automation rate (about 99%).

Add `--workflow exit` (or `all`) to play the five seed resignations too, and run `uv run relay demo`
to rehearse the PRD 22 five-minute demo: it lists each step, what to show, and whether the data it
needs is there.

**4. Open the HR console**

```bash
cd web
npm install
NEXT_PUBLIC_RELAY_API_URL=http://localhost:8000 npm run dev
```

Go to http://localhost:3000, sign in as a demo user (for example Asha, HR Ops), and look at the
dashboard, runs, the inbox of human tasks, policies, the audit log and the controls (kill switches,
fault injection). On Windows PowerShell, set the variable first:
`$env:NEXT_PUBLIC_RELAY_API_URL="http://localhost:8000"; npm run dev`.

**5. Optional: Slack.** Follow [docs/slack.md](docs/slack.md) once a workspace and tokens exist
(issue #21), then run `uv run relay slack`.

### Developing

```bash
docker compose up -d postgres     # just Postgres, on host port 55432
uv sync
uv run relay db upgrade
uv run relay validate             # every workflow against schema, policies and references
uv run relay policy test          # policy packs against their test cases
uv run pytest -q                  # DB tests skip if Postgres is not running
```

Quality gate (same as CI): `uv run ruff check relay tests && uv run ruff format --check relay tests && uv run mypy relay && uv run lint-imports && uv run pytest -q`.
On Windows, run `lint-imports` with `PYTHONIOENCODING=utf-8`.

## Team and AI tools

Built by a two-person team, each working with a Claude Code session (Anthropic). Coordination between sessions is logged in `CONVERSATION.md`.

## Licences

All runtime dependencies are permissively licensed. Check each at its pinned version (see `uv.lock` and
`web/package-lock.json`) before submission.

| Dependency | Licence | Used for |
|---|---|---|
| FastAPI, Pydantic, pydantic-settings | MIT | API and typed contracts |
| SQLAlchemy, Alembic | MIT | ORM and migrations |
| psycopg | LGPL-3.0 (used unmodified as a library) | Postgres driver |
| PostgreSQL, pgvector | PostgreSQL | System of record, queue, audit, vectors |
| cel-python | Apache-2.0 | Workflow and policy expressions |
| httpx | BSD-3-Clause | Connectors only |
| LiteLLM (optional `llm` extra) | MIT | Model router only |
| slack-bolt, slack-sdk | MIT | Slack app |
| Typer | MIT | CLI |
| uvicorn | BSD-3-Clause | ASGI server |
| cryptography | Apache-2.0 / BSD | Field encryption |
| PyJWT | MIT | Demo session tokens |
| PyYAML, jsonschema, python-multipart | MIT / MIT / Apache-2.0 | Definitions, schema checks, uploads |
| Next.js, React, TanStack Query, Tailwind CSS | MIT | HR console |
| pytest, ruff, mypy | MIT | Tests and quality gate |
| hypothesis | MPL-2.0 | Property-based tests (dev only) |
| import-linter | BSD-2-Clause | Architecture contracts (dev only) |
| matplotlib | PSF-based (Matplotlib licence) | Threshold report chart (dev only) |
| uv | MIT / Apache-2.0 | Packaging |

Model licences are chosen at eval time and must permit commercial use.
