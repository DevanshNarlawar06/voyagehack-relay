#!/usr/bin/env bash
# Append a message to CONVERSATION.md on main and push it (retries on races).
# Usage: scripts/post.sh <FROM A|B> <TO A|B|ALL> <TYPE> "<markdown body>"
#        scripts/post.sh B A QUESTION @path/to/body.md     (body from a file)
# TYPE: INFO TASK QUESTION ANSWER POLL VOTE DECISION BLOCKER HANDOFF REVIEW DONE
set -euo pipefail
FROM="${1:?from}"; TO="${2:?to}"; TYPE="${3:?type}"; shift 3
BODY="${*:?body}"
if [[ "$BODY" == @* ]]; then BODY="$(cat "${BODY#@}")"; fi
case "$FROM" in A|B) ;; *) echo "FROM must be A or B" >&2; exit 2 ;; esac
case "$TO" in A|B|ALL) ;; *) echo "TO must be A, B or ALL" >&2; exit 2 ;; esac

source "$(dirname "$0")/_comms_worktree.sh"
for attempt in 1 2 3 4 5 6; do
  comms_prepare
  F="$WT/CONVERSATION.md"
  N=$(( $(grep -cE "^### \[$FROM-[0-9]+\]" "$F" || true) + 1 ))
  ID="$(printf '%s-%04d' "$FROM" "$N")"
  TS="$(date -u +'%Y-%m-%d %H:%MZ')"
  printf '\n### [%s] %s · %s → %s · %s\n\n%s\n' "$ID" "$TS" "$FROM" "$TO" "$TYPE" "$BODY" >> "$F"
  git -C "$WT" add CONVERSATION.md
  if comms_push "chore(comms): $ID $TYPE $FROM->$TO"; then
    echo "posted $ID"
    exit 0
  fi
  sleep $(( attempt * 3 ))
done
echo "failed to post after retries" >&2
exit 1
