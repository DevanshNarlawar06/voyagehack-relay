# Live status

Each session edits **only its own block**, through `bash scripts/status.sh <A|B> "..."`. Read the latest from `origin/main` (`scripts/sync.sh` prints the other session's block).

<!-- STATUS:A:BEGIN -->
## Session A
_Updated 2026-11-07 08:55Z_

Now: merging #80 (#68), #81 (#79), #82 (#77), #83 (#78), #85 (#84) as their 45-min windows close (09:05-09:39Z), all CI green. Next: #77 employee lifecycle smoke checks once #80 lands. Blocked: needs-human #19 #20 #21 #22 #56. Branch: a/68-employee-lifecycle (+ worktrees for 77/78/84).
<!-- STATUS:A:END -->



<!-- STATUS:B:BEGIN -->
## Session B
_Updated 2026-11-07 09:45Z_

Now: #92 (#90) in review. Next: #91 data requests page by role, once #85 merges. Waiting on A: the erase and export message decision (#85 review, items 4 and 5). Needs a person: #56 consent wording, #20 LLM key. Branch: b/90-task-analysis.
<!-- STATUS:B:END -->
