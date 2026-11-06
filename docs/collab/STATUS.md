# Live status

Each session edits **only its own block**, through `bash scripts/status.sh <A|B> "..."`. Read the latest from `origin/main` (`scripts/sync.sh` prints the other session's block).

<!-- STATUS:A:BEGIN -->
## Session A
_Updated 2026-11-06 19:49Z_

**Now:** #32 policy bug fixes, then #4 WP4 engine. **Merged by A:** #29, #30, #34. **In review:** #31 (fix #26). **Waiting on B:** new PR for the router (old #28 auto-closed). **Blocked:** none. Only A merges (A-0007).
<!-- STATUS:A:END -->



<!-- STATUS:B:BEGIN -->
## Session B
_Updated 2026-11-06 19:43Z_

**Now:** #17 console (b/17-console). **In review:** #30 (#13), https://github.com/DevanshNarlawar06/voyagehack-relay/pull/33 (#14). **Merged:** #23, #24, #27. **Next:** #16 API on WP3/WP4. **Blocked:** A's #29 and #31 need a merge decision from Devansh.
<!-- STATUS:B:END -->
