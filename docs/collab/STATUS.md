# Live status

Each session edits **only its own block**, through `bash scripts/status.sh <A|B> "..."`. Read the latest from `origin/main` (`scripts/sync.sh` prints the other session's block).

<!-- STATUS:A:BEGIN -->
## Session A
_Updated 2026-11-06 20:03Z_

**Now:** #4 WP4 workflow engine (a/4-engine). **Merged by A:** #31, #36, #37 (+ #29, #30, #33, #34 earlier). **In review:** #39 (fix #38). **To review:** #35 console (waiting for web CI). **Blocked:** none.
<!-- STATUS:A:END -->



<!-- STATUS:B:BEGIN -->
## Session B
_Updated 2026-11-06 20:11Z_

**Now:** console against the real API (b/17-live). **In review:** https://github.com/DevanshNarlawar06/voyagehack-relay/pull/41 (#16), #35 (#17). **Next:** https://github.com/DevanshNarlawar06/voyagehack-relay/issues/40 after #4, then P1 #15, #18. **Blocked:** none. Only A merges.
<!-- STATUS:B:END -->
