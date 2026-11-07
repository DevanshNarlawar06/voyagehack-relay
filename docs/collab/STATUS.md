# Live status

Each session edits **only its own block**, through `bash scripts/status.sh <A|B> "..."`. Read the latest from `origin/main` (`scripts/sync.sh` prints the other session's block).

<!-- STATUS:A:BEGIN -->
## Session A
_Updated 2026-11-07 08:55Z_

Now: merging #80 (#68), #81 (#79), #82 (#77), #83 (#78), #85 (#84) as their 45-min windows close (09:05-09:39Z), all CI green. Next: #77 employee lifecycle smoke checks once #80 lands. Blocked: needs-human #19 #20 #21 #22 #56. Branch: a/68-employee-lifecycle (+ worktrees for 77/78/84).
<!-- STATUS:A:END -->



<!-- STATUS:B:BEGIN -->
## Session B
_Updated 2026-11-06 22:36Z_

**Stopped:** usage limit reached. **In review:** #57. **Not done:** policy acknowledgement route; real-path end-to-end run. **Branch:** b/e2e-live (driver, no PR). **Needs a person:** consent wording, LLM key, extension rehearsals.
<!-- STATUS:B:END -->
