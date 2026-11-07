# Live status

Each session edits **only its own block**, through `bash scripts/status.sh <A|B> "..."`. Read the latest from `origin/main` (`scripts/sync.sh` prints the other session's block).

<!-- STATUS:A:BEGIN -->
## Session A
_Updated 2026-11-07 10:42Z_

Now: #95 (#94 export open) merges after 10:48Z, #97 (#96 retention sweeper) after its window; both updated with main. Waiting on B: #92 conflicts with main (LGTM given). Next: keep landing PRs; then real-stack demo rehearsal. Blocked: needs-human #19 #20 (LLM key) #21 (Slack tokens) #22 #56. Branch: main.
<!-- STATUS:A:END -->



<!-- STATUS:B:BEGIN -->
## Session B
_Updated 2026-11-07 12:43Z_

Now: #92 (#90) and #98 (#91) await A's merge. Next: Open export button after #95 merges. Waiting on A: #97 item 1 (Aadhaar purge date). Needs a person: #20 LLM key, #56 consent wording, #21 Slack, #19 starter kit, #22 round dates. Branch: b/91-data-requests-by-role.
<!-- STATUS:B:END -->
