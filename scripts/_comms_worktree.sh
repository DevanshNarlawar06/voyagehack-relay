#!/usr/bin/env bash
# Shared helper: prepares a detached worktree at .comms/ that always matches origin/main.
# Coordination files (CONVERSATION.md, docs/collab/STATUS.md) are committed from there and
# pushed straight to main, so posting never disturbs your feature branch or working tree.
comms_prepare() {
  ROOT="$(git rev-parse --show-toplevel)"
  WT="$ROOT/.comms"
  git -C "$ROOT" fetch --quiet origin main
  if [ ! -e "$WT/.git" ]; then
    git -C "$ROOT" worktree prune
    git -C "$ROOT" worktree add --quiet --detach "$WT" origin/main
  fi
  git -C "$WT" fetch --quiet origin main
  git -C "$WT" reset --quiet --hard origin/main
}

# comms_push "<commit message>"  -> 0 on success, 1 if the push was rejected (caller retries)
comms_push() {
  git -C "$WT" commit --quiet -m "$1" || return 1
  git -C "$WT" push --quiet origin HEAD:main
}
