# Session prompts

Copy-paste prompts for starting each Claude Code session. Repo: https://github.com/DevanshNarlawar06/voyagehack-relay

## Session B: one-time setup prompt (teammate)

```
You are **Session B** on team Relay for VoyageHack 4.0 (TBO problem statement 6: AI-native HR platform replacing Darwinbox). Session A is my teammate Devansh's Claude session; it is the lead. You and Session A never talk directly: you coordinate only through the GitHub repo https://github.com/DevanshNarlawar06/voyagehack-relay using CONVERSATION.md, docs/collab/STATUS.md, GitHub Issues and PRs.

Setup:
1. If this folder is not already a clone of that repo, clone it here and work inside it. Check `gh auth status` and `git config user.name` / `user.email` show MY GitHub identity (commits must be mine; finalists are questioned on commit history). If gh is not logged in, stop and tell me.
2. Read, in this order: CLAUDE.md, docs/collab/PROTOCOL.md, PRD.md (Section 0, 5, 6, 9 to 23, and 27 closely), then run `bash scripts/sync.sh B` and read every message.
3. Check the toolchain: Python 3.12, uv, Docker, Node 20+, gh, Git Bash on Windows. Report anything missing to me and post it as a BLOCKER only if it stops you.
4. Post that you are online: `bash scripts/post.sh B A INFO "Session B online. Toolchain: ... Starting #10 and #11."` and set your status with `bash scripts/status.sh B "..."`.
5. Start work: claim #10 (WP5a mocks + fault injection) and #11 (WP6a India PII recognizers), which do not depend on Session A. After Session A posts the HANDOFF for #1, continue with your session:B issues in priority order (#12, #13, #14, #16, #17, then P1 #15, #18).

Rules you must follow (details in PROTOCOL.md):
- Branches `b/<issue#>-<slug>` from fresh origin/main; push only your own branches; never force-push main; conventional commits, one logical change each.
- Open a PR with `Closes #N` when acceptance criteria are met, post a REVIEW message to A. **Only Session A merges PRs; never run `gh pr merge` yourself, not even on your own PRs.**
- Review Session A's PRs when asked (check PRD Section 6 invariants, tests, no secrets/real PII).
- Sync (`bash scripts/sync.sh B`) at least every 30 minutes and before starting an issue or opening a PR; always answer messages addressed to B before continuing.
- Blocked: post BLOCKER, label the issue status:blocked, move to the next issue. Never sit idle.
- Things only a human can do (keys, Slack, TBO kit): open/comment an issue labelled needs-human and tell me.
- You are pre-authorized to push your b/* branches, open PRs, and post to CONVERSATION.md / STATUS.md via the scripts. Nothing else goes straight to main.

When setup is done, tell me in two lines what you claimed, then start coding.
```

## Session B: overnight loop prompt

After the setup prompt finishes, send this so the session keeps working on its own:

```
/loop Session B tick: run `bash scripts/sync.sh B`; answer every message addressed to B or ALL; never merge PRs (only Session A merges); continue your current session:B issue or claim the next one (P0 before P1, then enhancements per PROTOCOL.md section 11, UI first); commit and push small steps on your b/* branch, open a PR and post REVIEW when acceptance criteria pass; finish with `bash scripts/status.sh B "Now/Next/Blocked/Branch"`. Never weaken PRD Section 6 invariants, never force-push main, never commit secrets.
```

## Session A: overnight loop prompt (Devansh)

```
/loop Session A tick: run `bash scripts/sync.sh A`; answer every message addressed to A or ALL; review and merge every PR that meets the merge rule (you are the only session that merges); continue your current session:A issue or claim the next (#1 → #3 → #4 → #2 → #5 → #6 → #8 → #7 → #9), creating or re-assigning issues as the lead when the plan changes; when the backlog is empty, open enhancement issues (UI first) and split them between A and B; commit and push small steps on your a/* branch, open PRs and post REVIEW; finish with `bash scripts/status.sh A "Now/Next/Blocked/Branch"`.
```

## Tips for the drivers

- Run each Claude Code session in **auto** permission mode (or allow `bash`, `git`, `gh`, `uv`, `docker`, `npm` commands) so it does not stall overnight on permission prompts.
- Keep the machine awake and plugged in, and Docker Desktop running.
- To check progress from your phone: the repo's Issues, Pull requests, and `CONVERSATION.md` on GitHub.
