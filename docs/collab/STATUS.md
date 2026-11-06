# Live status

Each session edits **only its own block**, through `bash scripts/status.sh <A|B> "..."`. Read the latest from `origin/main` (`scripts/sync.sh` prints the other session's block).

<!-- STATUS:A:BEGIN -->
## Session A
_Updated 2026-11-06 20:27Z_

Now: #5 WP4b chaos test. In review: #47 (#44 engine fixes), #45 (#2 seed). Waiting on B: #41 named-human fix. Blocked: none. Branch: a/44-engine-fixes
<!-- STATUS:A:END -->



<!-- STATUS:B:BEGIN -->
## Session B
_Updated 2026-11-06 20:58Z_

**Now:** waiting for A to merge, then the engine gateway adapter. **In review:** #41 (API), #43 (live console), #46, #48, #49. **Built, PR held for #43:** #18 screens on `b/18-console-p1`; `/employees` API route to follow #41. **Blocked:** #15 acceptance needs an LLM key (#20).
<!-- STATUS:B:END -->
