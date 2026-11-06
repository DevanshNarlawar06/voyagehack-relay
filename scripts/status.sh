#!/usr/bin/env bash
# Replace your own block in docs/collab/STATUS.md on main and push it.
# Usage: scripts/status.sh A|B "<markdown: now / next / blocked / branch>"
#        scripts/status.sh B @path/to/status.md
set -euo pipefail
ME="${1:?usage: scripts/status.sh A|B text}"; shift
BODY="${*:?status text}"
if [[ "$BODY" == @* ]]; then BODY="$(cat "${BODY#@}")"; fi
case "$ME" in A|B) ;; *) echo "session must be A or B" >&2; exit 2 ;; esac

source "$(dirname "$0")/_comms_worktree.sh"
TMPB="$(mktemp)"; trap 'rm -f "$TMPB"' EXIT
for attempt in 1 2 3 4 5 6; do
  comms_prepare
  F="$WT/docs/collab/STATUS.md"
  {
    echo "## Session $ME"
    echo "_Updated $(date -u +'%Y-%m-%d %H:%MZ')_"
    echo
    printf '%s\n' "$BODY"
  } > "$TMPB"
  awk -v me="$ME" -v body="$TMPB" '
    $0 ~ "STATUS:" me ":BEGIN" { print; while ((getline l < body) > 0) print l; skip=1; next }
    $0 ~ "STATUS:" me ":END"   { skip=0 }
    !skip
  ' "$F" > "$F.new" && mv "$F.new" "$F"
  git -C "$WT" add docs/collab/STATUS.md
  if comms_push "chore(comms): status $ME"; then echo "status updated"; exit 0; fi
  sleep $(( attempt * 3 ))
done
echo "failed to update status after retries" >&2
exit 1
