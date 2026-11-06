# Live status

Each session edits **only its own block**, through `bash scripts/status.sh <A|B> "..."`. Read the latest from `origin/main` (`scripts/sync.sh` prints the other session's block).

<!-- STATUS:A:BEGIN -->
## Session A
_Updated 2026-11-06 19:19Z_

**Now:** PR #25 (WP1a, a/1-foundations) waiting for CI and review. Starting #3 WP3 policy engine on a/3-policy-engine. **Next:** #4 WP4 engine. **Blocked:** none.
<!-- STATUS:A:END -->



<!-- STATUS:B:BEGIN -->
## Session B
_Updated 2026-11-06 19:19Z_

**Now:** #14 model router (b/14-model-router), DB sinks to follow with #12. **In review:** #23 (#11), #24 (#10). **Next:** #12 after A's #1 HANDOFF. **Blocked:** none.
<!-- STATUS:B:END -->
