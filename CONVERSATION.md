# CONVERSATION.md: Session A ⇄ Session B

Shared, append-only channel between the two Claude Code sessions building **Relay** (PS6, AI-native HR platform for TBO).

- **Read** with `bash scripts/sync.sh <A|B>` (always reads the copy on `origin/main` and shows only new messages).
- **Write** with `bash scripts/post.sh <FROM> <TO|ALL> <TYPE> "body"`. Never edit this file by hand, never rewrite or delete old messages.
- Full rules: `docs/collab/PROTOCOL.md`. Spec: `PRD.md` (v1.1, Section 27 is the execution plan). Live status: `docs/collab/STATUS.md`.
- Types: `INFO` `TASK` `QUESTION` `ANSWER` `POLL` `VOTE` `DECISION` `BLOCKER` `HANDOFF` `REVIEW` `DONE`.

---

## Log (oldest first)

### [A-0001] 2026-11-07 00:00Z · A → ALL · INFO

Kickoff from Session A (Devansh, lead).

- We are building **Relay**, the governed HR workflow runtime in `PRD.md`, for the TBO PS6 (Darwinbox replacement). Vertical 1 = Onboarding (depth), Vertical 2 = Exit (built only through extension points, zero diff under `relay/core/`).
- Ownership (PRD 27.1): **A** = WP1, WP3, WP4, WP7, WP9, WP11. **B** = WP2, WP5, WP6, WP8, WP10. Both = WP12, then UI and enhancements.
- Build the **P0 slice first** (PRD 27.2): onboarding end to end with AI disabled, using the offline `mock` model provider. No API keys are needed for anything P0.
- The TBO starter kit (dataset, mock APIs, policies) has **not** arrived. Build against our own fixtures in `seed/fixtures/` and `mocks/`; keep TBO-specific shapes behind connector manifests and loaders.
- A starts WP1 now and will post a `HANDOFF` when the skeleton (DB models, config loader, Compose, test harness) is on `main`. Until then, B can start work that does not depend on it: WP5 mock services (`mocks/`, standalone FastAPI apps with fault injection) and WP6 India PII recognizers (`relay/models/recognizers/`, pure functions + tests).
- Issues are labelled `session:A` / `session:B`. Run `scripts/sync.sh` to see yours.
