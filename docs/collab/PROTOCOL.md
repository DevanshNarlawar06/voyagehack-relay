# Collaboration protocol: Session A ⇄ Session B

Two Claude Code sessions build Relay in parallel. They never talk directly; everything goes through this repo. This file is the contract. `CLAUDE.md` points every session here.

Start-up and loop prompts for each session: `docs/collab/SESSION_PROMPTS.md`.

## 1. Who is who

| Session | Driven by | Role |
|---|---|---|
| **A** | Devansh (`DevanshNarlawar06`) | **Lead and sole merger.** Creates and assigns most issues, final say on disputes, reviews and **merges every PR** (A's and B's), owns the spine (WP1, WP3, WP4, WP7, WP9, WP11). |
| **B** | Sarthak (`sarthakviche`) | **Builder.** Works `session:B` issues (WP2, WP5, WP6, WP8, WP10), may open issues for A, may open polls, reviews A's PRs. **Does not merge** any PR. |

Both share WP12 (docs) and, once P0 + P1 are done, UI and feature enhancements.

## 2. Sources of truth

| What | Where |
|---|---|
| Product spec | `PRD.md` (v1.1). Section 27 = execution plan. Never weaken a Section 6 invariant. |
| Messages, polls, hand-offs | `CONVERSATION.md` on **`origin/main`** (append-only) |
| Live "what am I doing" | `docs/collab/STATUS.md` on `origin/main` (each session edits only its own block) |
| Work items | GitHub Issues, labelled `session:A` / `session:B`, `wp:N`, `P0/P1/P2` |
| Code | Feature branches → PRs → `main` |
| Decisions | `DECISION` messages; architectural ones also get `docs/decisions/ADR-NNN-*.md` |

Always read `CONVERSATION.md` and `STATUS.md` from `origin/main` (the sync script does this), never from a stale local copy.

## 3. The sync checkpoint

Run `bash scripts/sync.sh <A|B>`. It fetches all branches, lists them by most recent commit, fast-forwards `main` if you are on it, prints **new** messages since your last sync (marking ones addressed to you), your open issues, `needs-human` issues, open PRs and the other session's status.

Run it:
- at session start and at every loop tick;
- before starting an issue, before opening a PR, after merging;
- at least every 30 minutes while coding.

After every sync: **answer every message addressed to you** (`→ A`, `→ B` or `→ ALL`) that needs a reply, before going back to code.

## 4. Posting

```bash
bash scripts/post.sh <FROM> <TO|ALL> <TYPE> "markdown body"
bash scripts/status.sh <ME> "**Now:** #12 policy engine (a/12-policy-engine)  **Next:** #14  **Blocked:** none"
```

Both scripts commit from a hidden `.comms/` worktree straight to `main` and retry on push races, so they never touch your feature branch or uncommitted work. Never edit `CONVERSATION.md` by hand on a feature branch.

Message types: `INFO`, `TASK`, `QUESTION`, `ANSWER`, `POLL`, `VOTE`, `DECISION`, `BLOCKER`, `HANDOFF`, `REVIEW`, `DONE`.

Message header (written by the script): `### [B-0007] 2026-11-07 21:40Z · B → A · QUESTION`. Reference earlier messages by id (`re A-0003`) and issues by `#N`.

### Polls
```
**Poll P-3: tier1 model**
- (a) ...
- (b) ...
Closes: when both voted, or 2026-11-08 06:00Z. Default if no votes: (a).
```
Reply with a `VOTE` message (`P-3: (b), because ...`). Session A posts the `DECISION` (and an ADR if it is architectural). If A is silent past the close time, the default stands.

## 5. Branches and commits

- Branch from fresh `origin/main`: `a/<issue#>-<slug>` or `b/<issue#>-<slug>` (e.g. `b/7-audit-chain`).
- Only push to your own prefix. Never commit to the other session's branches. Never force-push `main`. Force-push your own branch only with `--force-with-lease`.
- Conventional commits, one logical change per commit (`feat(engine): lease steps with SKIP LOCKED`). Finalists are questioned on commit history, so commits must carry the driver's own git identity.
- Rebase on `origin/main` before opening or updating a PR.
- Coordination-only commits (`chore(comms): ...`) go straight to `main` via the scripts. Nothing else goes straight to `main`.

## 6. Issues

- Session A creates most issues; B may create any issue and assign it with a label.
- Labels: `session:A|B`, `wp:1..12`, `P0|P1|P2`, `type:task|bug|question|enhancement`, `status:in-progress`, `status:blocked`, `needs-human`.
- Claim before working: add `status:in-progress` and comment `claimed by Session X on branch ...`.
- Close only through a PR (`Closes #N`) or with a comment explaining why.
- Anything only a human can do (API keys, Slack workspace, TBO starter kit, round dates, repo settings) → issue with `needs-human`, and mention it in your status block.

## 7. Pull requests

- Title in conventional-commit style; body: `Closes #N`, what changed, how it was tested, anything the other session must know.
- After opening, post a `REVIEW` message to the other session with the PR link.
- **Only Session A merges** (decision A-0007, by Devansh). Session B never runs `gh pr merge`, not even on its own PRs.
- **Merge rule (applied by A):** CI green **and** reviewed: for B's PRs, A's own review; for A's PRs, B approved/commented "LGTM", **or** 45 minutes have passed since A's `REVIEW` message with no objection. A merges with `gh pr merge <N> --merge --delete-branch` (merge commit, keeps individual history) and resolves conflicts on `main` when needed.
- Stacked PRs: when A merges a base PR, A retargets or reopens the PRs stacked on it.
- The reviewer checks: PRD invariants (Section 6), tests exist, no secrets or real PII, no edits in the other session's area without a prior message.

## 8. Ownership and shared files

| Area | Owner |
|---|---|
| `relay/db/`, `docker-compose.yml`, `pyproject.toml` (initial), `relay/core/{engine,policy,ledger,registry,dsl}`, `relay/rules/`, `definitions/workflows/`, `definitions/policies/`, `relay/slack/`, `relay/cli/` | A |
| `relay/core/{audit,cost}`, `relay/connectors/`, `mocks/`, `relay/models/`, `relay/skills/`, `definitions/{skills,prompts,connectors}`, `evals/`, `relay/api/`, `web/` | B |
| `docs/`, `seed/`, `tests/` (each tests its own code), `README.md` | shared |

- Small additive edits to shared files (a dependency in `pyproject.toml`, a service in `docker-compose.yml`, a new table in models) are fine; mention them in the PR body.
- Alembic migrations: one per WP, name it `NNNN_wpX_<slug>.py`; if two heads appear, Session A adds the merge migration when merging.
- Need to change the other session's area? Post a `QUESTION` or `TASK` first, or open an issue for them.

## 9. Blocked, conflicts, disagreements

- Blocked → post `BLOCKER`, set `status:blocked` on the issue, update status, and **switch to the next unblocked issue**. Never sit idle.
- Merge conflict → whoever hits it resolves it; if it is in the other session's code, keep their intent and tell them.
- Disagreement → one `POLL`, Session A decides. The PRD wins over opinions; changes to the PRD need a `DECISION` message from A.

## 10. Overnight autonomous mode

Each session runs on a self-paced loop. Every tick:
1. `bash scripts/sync.sh <ME>` and reply to messages addressed to you.
2. Continue the current issue, or claim the next open issue for your session in priority order (P0 → P1 → P2, lower WP first).
3. Commit small, push your branch, open a PR when the acceptance criteria in PRD Section 23 are met, post `REVIEW`.
4. **Session A only:** merge every PR that satisfies the merge rule. Session B: post `REVIEW` for each ready PR and leave merging to A.
5. `bash scripts/status.sh <ME> "..."` with now / next / blocked / branch.

Never, in any mode: force-push `main`; delete branches that are not your own merged ones; change repo settings or secrets; commit `.env`, keys or real personal data; disable tests or CI to get green; weaken a PRD Section 6 invariant.

## 11. When the backlog is empty

Post `DONE` with what shipped. Then work on enhancements, **UI first** (console polish, run DAG view, dashboard charts, Slack cards), then features from PRD P2. To avoid duplicate work, open an issue (`type:enhancement`) and claim it before starting, and announce it with an `INFO` message.
