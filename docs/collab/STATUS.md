# Live status

Each session edits **only its own block**, through `bash scripts/status.sh <A|B> "..."`. Read the latest from `origin/main` (`scripts/sync.sh` prints the other session's block).

<!-- STATUS:A:BEGIN -->
## Session A
_Updated 2026-11-07 06:33Z_

Now: #74 (#71) in review. Next: more enhancements once #74 lands; B owns #66-#68 (console). Blocked: tokens (#21), LLM key (#20), TBO kit (#19). Branch: a/71-exit-sim-demo
<!-- STATUS:A:END -->



<!-- STATUS:B:BEGIN -->
## Session B
_Updated 2026-11-06 22:36Z_

**Stopped:** usage limit reached. **In review:** #57. **Not done:** policy acknowledgement route; real-path end-to-end run. **Branch:** b/e2e-live (driver, no PR). **Needs a person:** consent wording, LLM key, extension rehearsals.
<!-- STATUS:B:END -->
