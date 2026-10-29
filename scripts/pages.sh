#!/bin/sh
# Builds the GitHub Pages site and writes it to the `pages` branch (GitHub serves that branch's root).
#   scripts/pages.sh          # build/pages/, then update the local pages branch
#   scripts/pages.sh --push   # also push it to origin
#
# The site is docs/ (three guides, style and script inlined by docs/build.py) plus the WebMIDI editor from
# tools/web/ as editor/. The branch is written with git plumbing, so the working tree and the current branch are
# left alone; it holds one commit with no history, replaced only when the site changes, which is why the push is
# forced. A checkout of the branch (git worktree) is reset to the new commit.
#
# Environment: PYTHON (default python3).
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

PUSH=0
for arg in "$@"; do
  case "$arg" in
    --push) PUSH=1 ;;
    -h|--help) sed -n '2,/^set -eu/{/^set -eu/d;s/^# \{0,1\}//;p;}' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

PYTHON=${PYTHON:-python3}
OUT=$ROOT/build/pages
rm -rf "$OUT"
mkdir -p "$OUT/editor"
"$PYTHON" -I docs/build.py docs "$OUT"
cp tools/web/index.html tools/web/panel-map.json "$OUT/editor/"
if grep -rl -E '/Users/|/home/' "$OUT"; then
  echo "pages: build paths in the site, not publishing" >&2
  exit 1
fi

git rev-parse --git-dir >/dev/null 2>&1 || { echo "== pages: not a git checkout, skipped"; exit 0; }
GITDIR=$(git rev-parse --absolute-git-dir)
INDEX=$ROOT/build/pages.index
rm -f "$INDEX"
TREE=$(cd "$OUT" && GIT_DIR=$GITDIR GIT_WORK_TREE=$OUT GIT_INDEX_FILE=$INDEX git add -A . \
  && GIT_DIR=$GITDIR GIT_WORK_TREE=$OUT GIT_INDEX_FILE=$INDEX git write-tree)
rm -f "$INDEX"

if [ "$(git rev-parse -q --verify 'refs/heads/pages^{tree}' || true)" = "$TREE" ]; then
  echo "== pages: unchanged"
else
  SRC=$(git rev-parse --short HEAD)
  git diff --quiet HEAD -- || SRC="$SRC with local changes"
  git update-ref refs/heads/pages "$(git commit-tree "$TREE" -m "PRO-800 firmware $(cat VERSION): the guides and the editor, built from $SRC")"
  echo "== pages: built from $SRC"
  WT=$(git worktree list --porcelain | awk '/^worktree /{w=substr($0,10)} /^branch refs\/heads\/pages$/{print w}')
  if [ -n "$WT" ]; then
    git -C "$WT" reset -q --hard refs/heads/pages
    echo "== pages: checkout at $WT reset"
  fi
fi

if [ "$PUSH" = 1 ]; then
  if git rev-parse -q --verify refs/remotes/origin/pages >/dev/null; then
    git push --force-with-lease=pages:refs/remotes/origin/pages origin pages
  else
    git push -u origin pages
  fi
fi
