# Live status

Each session edits **only its own block**, through `bash scripts/status.sh <A|B> "..."`. Read the latest from `origin/main` (`scripts/sync.sh` prints the other session's block).

<!-- STATUS:A:BEGIN -->
## Session A
_Updated 2026-11-07 10:42Z_

Now: #95 (#94 export open) merges after 10:48Z, #97 (#96 retention sweeper) after its window; both updated with main. Waiting on B: #92 conflicts with main (LGTM given). Next: keep landing PRs; then real-stack demo rehearsal. Blocked: needs-human #19 #20 (LLM key) #21 (Slack tokens) #22 #56. Branch: main.
<!-- STATUS:A:END -->



<!-- STATUS:B:BEGIN -->
## Session B
_Updated 2026-11-07 09:45Z_

Now: #92 (#90) in review. Next: #91 data requests page by role, once #85 merges. Waiting on A: the erase and export message decision (#85 review, items 4 and 5). Needs a person: #56 consent wording, #20 LLM key. Branch: b/90-task-analysis.
<!-- STATUS:B:END -->
