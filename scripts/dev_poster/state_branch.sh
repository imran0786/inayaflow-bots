#!/usr/bin/env bash
# Finds out, with the SAME kind of command the other robots use (a plain clone), whether the private state branch exists.
# usage: state_branch.sh <repo url> <dest dir> <branch>
#   exit 0  + prints EXISTS       the branch exists; DEST is a clone with that branch checked out
#   exit 10 + prints ABSENT       the repository was cloned fine but has no such branch (first run); DEST is that clone (default branch)
#   exit 20 + prints UNREACHABLE  the repository could not be cloned (network, token, wrong URL): the caller must NOT run / NOT save
# Prints only these words (never the URL, which may carry a token).
URL="$1"; DEST="$2"; BR="$3"
export GIT_TERMINAL_PROMPT=0
rm -rf "$DEST"
if ! git clone -q --no-single-branch --depth 1 "$URL" "$DEST" >/dev/null 2>&1; then
  echo UNREACHABLE
  exit 20
fi
if git -C "$DEST" show-ref --verify --quiet "refs/remotes/origin/$BR"; then
  if git -C "$DEST" checkout -q -B "$BR" "origin/$BR" >/dev/null 2>&1; then
    echo EXISTS
    exit 0
  fi
  echo UNREACHABLE
  exit 20
fi
echo ABSENT
exit 10
