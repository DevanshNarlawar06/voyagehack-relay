# ADR-001: Two Claude Code sessions coordinate through the repo

- Status: accepted
- Date: 2026-11-07
- Deciders: Devansh (Session A)

## Context
Two team members each drive a Claude Code session and will build overnight with little live supervision. The sessions cannot talk to each other directly.

## Decision
They coordinate only through git: `CONVERSATION.md` (append-only messages and polls on `main`), `docs/collab/STATUS.md` (each session's live status), GitHub Issues (work items, labelled `session:A` / `session:B`) and PRs (code). Protocol in `docs/collab/PROTOCOL.md`; helper scripts in `scripts/`.

## Consequences
Every decision and hand-off is visible in git history, which also serves the "individual contribution" rule. Messages are asynchronous, so each session syncs at fixed checkpoints.
