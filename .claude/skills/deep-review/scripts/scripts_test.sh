#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
# Tests branch_context.sh and preflight.sh against a throwaway repository.
# Run: bash .claude/skills/deep-review/scripts/scripts_test.sh
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "ok: $*"; }

export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com
export GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
git init -q -b main "$tmp/repo"
cd "$tmp/repo"
seq 1 50 >notes.txt
seq 1 50 >tool.py
git add -A
git commit -q -s -m init

# The base renames notes.txt away; the head edits it.
git checkout -q -b head
echo 51 >>notes.txt
git commit -q -s -am "edit notes"
git checkout -q main
git mv notes.txt moved.txt
git commit -q -s -m "rename notes"

out=$(bash "$here/branch_context.sh" head main)
jq -e '.changed_files == ["notes.txt"]' <<<"$out" >/dev/null || fail "changed_files: $(jq -c .changed_files <<<"$out")"
jq -e '.overlapping_files_with_base == ["notes.txt"]' <<<"$out" >/dev/null \
  || fail "a head edit to a file the base renamed away is not overlapping: $(jq -c .overlapping_files_with_base <<<"$out")"
pass "branch_context.sh reports a file the base renamed away as overlapping"

# The head moves tool.py into a build output directory.
git checkout -q head
mkdir -p dist
git mv tool.py dist/tool.py
git commit -q -s -m "move tool"
out=$(bash "$here/preflight.sh" head~1 head)
added=$(sed -n '/^## Added artifact candidates$/,/^## /p' <<<"$out")
deleted=$(sed -n '/^## Deleted files$/,/^## /p' <<<"$out")
grep -qF -- '- "dist/tool.py"' <<<"$added" || fail "a file renamed into dist/ is not an added artifact candidate: $added"
grep -qF -- '- "tool.py"' <<<"$deleted" || fail "a file renamed away is not a deleted file: $deleted"
pass "preflight.sh lists renamed files under added artifacts and deleted files"

echo "all deep-review script tests passed"
