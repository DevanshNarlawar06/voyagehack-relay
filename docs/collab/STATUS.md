# Live status

Each session edits **only its own block**, through `bash scripts/status.sh <A|B> "..."`. Read the latest from `origin/main` (`scripts/sync.sh` prints the other session's block).

<!-- STATUS:A:BEGIN -->
## Session A
_Updated 2026-11-06 19:58Z_

**Now:** #4 WP4 workflow engine (a/4-engine). **Merged by A:** #29, #30, #33, #34. **In review:** #31 (fix #26), #37 (fix #32). **Blocked:** none.
<!-- STATUS:A:END -->



<!-- STATUS:B:BEGIN -->
## Session B
_Updated 2026-11-06 19:56Z_

**Now:** #16 API. **In review:** https://github.com/DevanshNarlawar06/voyagehack-relay/pull/35 (#17). **Merged:** #10, #11, #12, #14, #13 connectors (#13 open for the engine test). **Next:** audit hook wiring, then P1 #15, #18. **Blocked:** none. Only A merges.
<!-- STATUS:B:END -->
