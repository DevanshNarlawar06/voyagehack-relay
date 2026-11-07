# Live status

Each session edits **only its own block**, through `bash scripts/status.sh <A|B> "..."`. Read the latest from `origin/main` (`scripts/sync.sh` prints the other session's block).

<!-- STATUS:A:BEGIN -->
## Session A
_Updated 2026-11-07 10:06Z_

Now: #93 (#89 correction details, honest DPDP messages) and #95 (#94 audited export open) in review. Merged today: #76 #80 #81 #82 #83 #85 #86 #87 #88. Waiting on B to rebase #92 (LGTM given). Next: tidy worktrees, then more UI follow-ups. Blocked: needs-human #19 #20 #21 #22 #56. Branch: main.
<!-- STATUS:A:END -->



<!-- STATUS:B:BEGIN -->
## Session B
_Updated 2026-11-07 09:45Z_

Now: #92 (#90) in review. Next: #91 data requests page by role, once #85 merges. Waiting on A: the erase and export message decision (#85 review, items 4 and 5). Needs a person: #56 consent wording, #20 LLM key. Branch: b/90-task-analysis.
<!-- STATUS:B:END -->
