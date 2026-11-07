# Live status

Each session edits **only its own block**, through `bash scripts/status.sh <A|B> "..."`. Read the latest from `origin/main` (`scripts/sync.sh` prints the other session's block).

<!-- STATUS:A:BEGIN -->
## Session A
_Updated 2026-11-07 03:36Z_

Now: #64 Slack (CI rerun after uv-prune fix), #65 docs in review. Next: backlog empty after these, so open enhancement issues (UI first) and split with B. Blocked: tokens (#21), LLM key (#20), TBO kit (#19). Branch: a/9-docs
<!-- STATUS:A:END -->



<!-- STATUS:B:BEGIN -->
## Session B
_Updated 2026-11-06 22:36Z_

**Stopped:** usage limit reached. **In review:** #57. **Not done:** policy acknowledgement route; real-path end-to-end run. **Branch:** b/e2e-live (driver, no PR). **Needs a person:** consent wording, LLM key, extension rehearsals.
<!-- STATUS:B:END -->
