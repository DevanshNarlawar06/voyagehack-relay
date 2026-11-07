# Live status

Each session edits **only its own block**, through `bash scripts/status.sh <A|B> "..."`. Read the latest from `origin/main` (`scripts/sync.sh` prints the other session's block).

<!-- STATUS:A:BEGIN -->
## Session A
_Updated 2026-11-07 02:37Z_

Now: #7 Slack app on a/7-slack (committed, local). Merging #63 at 02:47Z, then resign-to-exit test, full gate, PR. Next: #9 docs. Blocked: real Slack run needs tokens (#21). Branch: a/7-slack
<!-- STATUS:A:END -->



<!-- STATUS:B:BEGIN -->
## Session B
_Updated 2026-11-06 22:36Z_

**Stopped:** usage limit reached. **In review:** #57. **Not done:** policy acknowledgement route; real-path end-to-end run. **Branch:** b/e2e-live (driver, no PR). **Needs a person:** consent wording, LLM key, extension rehearsals.
<!-- STATUS:B:END -->
