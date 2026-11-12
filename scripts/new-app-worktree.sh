#!/bin/bash
# Create an app-only TrekDex worktree with a cone-mode sparse checkout.
#
# Usage:
#   scripts/new-app-worktree.sh <slug> [base-ref]
#
#   <slug>      worktree folder name; the branch is chat/<slug>
#   [base-ref]  commit to branch from (default origin/main, fetched first)
#
# What is left out: public/areas/silhouettes/ (~9.4k files, ~220 MB).
# The app fetches silhouettes at runtime; nothing in ios/ or the bundle
# filter reads them. Everything else is checked out:
#   - every other top-level directory (ios, scripts, trailforge, ...)
#   - public/areas/*.json (index.json and aliases.json feed
#     scripts/filter-ios-bundle.py)
#   - public/areas/geom/ (the filter's per-area gate, and the
#     SouthMountain* unit tests read geom files directly)
# Cone mode always includes files directly under each listed directory's
# parents, which is how public/areas/*.json come along with geom/.
#
# Need the rest later? From inside the worktree:
#   git sparse-checkout disable
#
# Environment (optional):
#   TREKDEX_WORKTREES_DIR  where worktrees go (default: the
#                          "South Mountain Explorer Worktrees" folder next
#                          to the main checkout)
#
# Exit codes: 0 created, 2 usage, 3 destination or branch already exists.
set -euo pipefail

usage() { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
[ $# -ge 1 ] && [ $# -le 2 ] || usage
SLUG="$1"
BASE="${2:-origin/main}"
case "$SLUG" in
  ""|.*|*/*|*[!A-Za-z0-9._-]*) echo "bad slug: $SLUG" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMMON="$(git -C "$SCRIPT_DIR" rev-parse --path-format=absolute --git-common-dir)"
MAIN="$(dirname "$COMMON")"
WT_ROOT="${TREKDEX_WORKTREES_DIR:-$(dirname "$MAIN")/South Mountain Explorer Worktrees}"
DEST="$WT_ROOT/$SLUG"
BRANCH="chat/$SLUG"

if [ -e "$DEST" ]; then echo "already exists: $DEST" >&2; exit 3; fi
if git -C "$MAIN" show-ref --verify --quiet "refs/heads/$BRANCH"; then
  echo "branch already exists: $BRANCH" >&2; exit 3
fi
case "$BASE" in origin/*) git -C "$MAIN" fetch --quiet origin ;; esac

git -C "$MAIN" worktree add --quiet --no-checkout -b "$BRANCH" "$DEST" "$BASE"

# Every top-level directory except public/, plus public/areas/geom.
DIRS=()
while IFS= read -r d; do
  [ "$d" = public ] || DIRS+=("$d")
done < <(git -C "$DEST" ls-tree -d --name-only HEAD)
DIRS+=("public/areas/geom")

git -C "$DEST" sparse-checkout set --cone "${DIRS[@]}"
git -C "$DEST" checkout --quiet

FILES="$(git -C "$DEST" ls-files -t | grep -c '^H' || true)"
TOTAL="$(git -C "$DEST" ls-tree -r --name-only HEAD | wc -l | tr -d ' ')"
echo "Created $DEST"
echo "  branch $BRANCH from $BASE ($(git -C "$DEST" rev-parse --short HEAD))"
echo "  sparse checkout: $FILES of $TOTAL tracked files (public/areas/silhouettes left out)"
