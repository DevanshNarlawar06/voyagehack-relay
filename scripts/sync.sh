#!/usr/bin/env bash
# Sync checkpoint for a Claude session.
# Usage: scripts/sync.sh A|B
# Fetches every branch, shows which branches moved most recently, updates main if safe,
# prints NEW messages in CONVERSATION.md (from origin/main), your open issues, open PRs
# and the other session's status block.
set -euo pipefail
ME="${1:?usage: scripts/sync.sh A|B}"
case "$ME" in A) OTHER=B ;; B) OTHER=A ;; *) echo "session must be A or B" >&2; exit 2 ;; esac

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
GITDIR="$(git rev-parse --git-common-dir)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

git fetch --all --prune --quiet

echo "================ BRANCHES (most recent first) ================"
git for-each-ref --sort=-committerdate refs/remotes/origin --count=15 \
  --format='%(committerdate:relative)|%(refname:short)|%(authorname)|%(subject)' \
  | awk -F'|' '$2!="origin/HEAD" && $2!="origin" {printf "%-16s %-40s %-18s %s\n", $1, $2, $3, substr($4,1,70)}'

echo
CUR="$(git branch --show-current || true)"
BEHIND="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo '?')"
echo "================ YOUR CHECKOUT ================"
echo "branch: ${CUR:-detached}   origin/main is ahead of you by: $BEHIND commit(s)"
if [ "$CUR" = "main" ]; then
  if [ -z "$(git status --porcelain)" ]; then
    git pull --rebase --quiet origin main && echo "main fast-forwarded to origin/main"
  else
    echo "main has local changes: commit or stash them, then: git pull --rebase origin main"
  fi
elif [ "$BEHIND" != "0" ] && [ "$BEHIND" != "?" ]; then
  echo "feature branch is behind main: run 'git rebase origin/main' before opening/updating a PR"
fi

echo
echo "================ NEW MESSAGES (CONVERSATION.md @ origin/main) ================"
git show origin/main:CONVERSATION.md > "$TMP/conv.md" 2>/dev/null || { echo "(no CONVERSATION.md on origin/main yet)"; : > "$TMP/conv.md"; }
STATE="$GITDIR/collab-last-read-$ME"
LAST="$(cat "$STATE" 2>/dev/null || true)"
awk -v last="$LAST" -v me="$ME" '
  /^### \[[AB]-[0-9]+\]/ {
    id=$2; gsub(/[\[\]]/,"",id)
    if (seen || last=="") show=1
    if (id==last) { seen=1; show=0 }
    if (show) {
      hdr=$0
      if (hdr ~ ("→ " me " ") || hdr ~ "→ ALL ") print "\n>>> FOR YOU >>>"
      print hdr; inmsg=1; next
    }
    inmsg=0; next
  }
  /^## / { inmsg=0 }
  { if (show && inmsg) print }
' "$TMP/conv.md"
NEWLAST="$(grep -oE '^### \[[AB]-[0-9]+\]' "$TMP/conv.md" | tail -1 | grep -oE '[AB]-[0-9]+' || true)"
if [ -n "$NEWLAST" ]; then
  if [ "$NEWLAST" = "$LAST" ]; then echo "(no new messages since $LAST)"; fi
  echo "$NEWLAST" > "$STATE"
fi

echo
echo "================ OPEN ISSUES for session:$ME ================"
gh issue list --label "session:$ME" --state open --limit 40 2>/dev/null || echo "(gh not available or not authenticated)"
echo
echo "================ ISSUES needing a human ================"
gh issue list --label "needs-human" --state open --limit 20 2>/dev/null || true
echo
echo "================ OPEN PULL REQUESTS ================"
gh pr list --limit 20 2>/dev/null || true

echo
echo "================ SESSION $OTHER STATUS ================"
git show origin/main:docs/collab/STATUS.md 2>/dev/null \
  | awk -v o="$OTHER" '$0 ~ "STATUS:" o ":BEGIN" {p=1; next} $0 ~ "STATUS:" o ":END" {p=0} p' || true
