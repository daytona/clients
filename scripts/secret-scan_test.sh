#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# End-to-end test of the secret-scanning hooks: builds a throwaway repository
# with this checkout's .gitleaks.toml, .githooks/ and scripts/secret-scan.sh,
# then commits and pushes through real git. Needs gitleaks: the pinned binary
# (scripts/secret-scan.sh install-bin) or one on PATH.
# Run: bash scripts/secret-scan_test.sh
set -euo pipefail

src=$(cd "$(dirname "$0")/.." && pwd)

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "ok: $*"; }

# Use the source checkout's pinned gitleaks when it has one.
pinned_dir="$(cd "$src" && cd "$(git rev-parse --git-common-dir)" && pwd)/gitleaks"
version=$(sed -n 's/^GITLEAKS_VERSION=//p' "$src/scripts/secret-scan.sh")
if [ -x "$pinned_dir/$version/gitleaks" ]; then
  PATH="$pinned_dir/$version:$PATH"
fi
command -v gitleaks >/dev/null 2>&1 || fail "gitleaks not found; run scripts/secret-scan.sh install-bin"

# Isolate from the caller's git configuration (global hooks, signing, ...).
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com
export GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# Credentials are assembled at run time so this file holds none.
hex() { od -An -tx1 -N"$1" /dev/urandom | tr -d ' \n'; }
alnum() { (LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom || true) | head -c "$1"; } # tr ends on SIGPIPE
daytona_key="dtn_$(hex 16)"
github_token="ghp_$(alnum 36)"

git init -q --bare "$tmp/remote.git"
git init -q -b main "$tmp/repo"
cd "$tmp/repo"
mkdir -p scripts
cp "$src/.gitleaks.toml" .
cp -R "$src/.githooks" .
cp "$src/scripts/secret-scan.sh" scripts/
git remote add origin "$tmp/remote.git"

# ── install: hooks path ────────────────────────────────────────────────────
scripts/secret-scan.sh install >"$tmp/out" 2>&1 || fail "install failed: $(cat "$tmp/out")"
[ "$(git config --local core.hooksPath)" = .githooks ] || fail "install did not set core.hooksPath"
pass "install sets core.hooksPath=.githooks"

git add -A
git commit -q -m "tooling" || fail "clean commit was blocked"
pass "clean commit passes"

# ── pre-commit ─────────────────────────────────────────────────────────────
echo "DAYTONA_API_KEY=$daytona_key" >config.env
git add config.env
if git commit -q -m "leak" >"$tmp/out" 2>&1; then fail "commit with a Daytona API key was not blocked"; fi
grep -q "daytona-api-key" "$tmp/out" || fail "block did not name the rule: $(cat "$tmp/out")"
grep -qF "$daytona_key" "$tmp/out" && fail "the secret was printed unredacted"
[ "$(git rev-list --count HEAD)" = 1 ] || fail "a commit was created despite the block"
pass "commit with a Daytona API key is blocked, redacted"

echo "token: $github_token" >config.env
git add config.env
if git commit -q -m "leak" >/dev/null 2>&1; then fail "commit with a GitHub token (default rules) was not blocked"; fi
pass "commit with a GitHub token (default rules) is blocked"

# Partially staged: the scan reads the index, not the working tree.
echo "harmless" >config.env
git add config.env
echo "DAYTONA_API_KEY=$daytona_key" >config.env
git commit -q -m "staged blob is clean" || fail "clean staged blob was blocked by an unstaged secret"
pass "only staged content is scanned"
git checkout -q -- config.env

echo "example = \"$daytona_key\" # gitleaks:allow" >fixture.txt
git add fixture.txt
git commit -q -m "allowed fixture" || fail "gitleaks:allow line was blocked"
pass "gitleaks:allow suppresses one line"

# ── private rules ──────────────────────────────────────────────────────────
mkdir -p .git/info
cat >.git/info/gitleaks-private.toml <<'EOF'
[[rules]]
id = "private-codename"
description = "internal codename"
regex = '''\bzebra-codename\b'''
keywords = ["zebra-codename"]
EOF
echo "see zebra-codename" >notes.txt
git add notes.txt
if git commit -q -m "private" >"$tmp/out" 2>&1; then fail "private rule did not block"; fi
grep -q "private-codename" "$tmp/out" || fail "private block did not name the rule"
echo "see zebra-codename # gitleaks:allow" >notes.txt
git add notes.txt
if git commit -q -m "private" >/dev/null 2>&1; then fail "gitleaks:allow bypassed a private rule"; fi
rm .git/info/gitleaks-private.toml
git commit -q -m "private rule gone" || fail "commit blocked after the private rules were removed"
pass "private rules in .git/info/gitleaks-private.toml apply, with no inline exception"

# ── chained clone hook ─────────────────────────────────────────────────────
printf '#!/bin/sh\ntouch "%s/chained"\n' "$tmp" >.git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
echo one >chain.txt
git add chain.txt
git commit -q -m "chain" || fail "commit with a chained hook failed"
[ -f "$tmp/chained" ] || fail ".git/hooks/pre-commit was not chained"
rm .git/hooks/pre-commit
pass "the clone's own pre-commit hook still runs"

# ── pre-push ───────────────────────────────────────────────────────────────
git push -q origin main 2>"$tmp/out" || fail "clean push was blocked: $(cat "$tmp/out")"
pass "clean push passes"

echo "DAYTONA_API_KEY=$daytona_key" >bypass.env
git add bypass.env
git commit -q --no-verify -m "bypassed pre-commit"
if git push -q origin main >"$tmp/out" 2>&1; then fail "push of a --no-verify commit with a secret was not blocked"; fi
grep -q "daytona-api-key" "$tmp/out" || fail "push block did not name the rule"
[ "$(git rev-parse main)" != "$(git --git-dir="$tmp/remote.git" rev-parse main)" ] || fail "remote was updated"
pass "push of a --no-verify commit with a secret is blocked"

if git push -q origin main:refs/heads/other >/dev/null 2>&1; then fail "push of the secret to a new branch was not blocked"; fi
pass "push to a new branch scans the commits the remote lacks"

if git push -q "$tmp/remote.git" main:refs/heads/by-url >/dev/null 2>&1; then fail "push of the secret to a bare URL was not blocked"; fi
pass "push to a bare URL (no remote-tracking refs) is scanned"

# History already on the remote (pushed past the hooks) is not rescanned, so
# an old finding does not block every later push.
git push -q --no-verify origin main:refs/heads/legacy
echo clean >on-top.txt
git add on-top.txt
git commit -q -m "clean on top of history the remote has"
git push -q origin HEAD:refs/heads/legacy-2 2>"$tmp/out" || fail "push blocked by a commit already on the remote: $(cat "$tmp/out")"
pass "commits already on the remote are not rescanned"
git reset -q --hard HEAD~2

git push -q origin main:refs/heads/other 2>"$tmp/out" || fail "clean push to a new branch was blocked: $(cat "$tmp/out")"
git push -q origin :refs/heads/other 2>"$tmp/out" || fail "branch deletion was blocked: $(cat "$tmp/out")"
pass "new clean branch and branch deletion pass"

# ── range (CI) ─────────────────────────────────────────────────────────────
base=$(git rev-parse HEAD)
echo "DAYTONA_API_KEY=$daytona_key" >ci.env
git add ci.env
git commit -q --no-verify -m "ci leak"
if scripts/secret-scan.sh range "$base..HEAD" >/dev/null 2>&1; then fail "range scan missed the secret"; fi
scripts/secret-scan.sh range "$base~1..$base" >/dev/null 2>&1 || fail "range scan of a clean range failed"
pass "range scans exactly the given commits"

rc=0
scripts/secret-scan.sh range "0123456789abcdef0123456789abcdef01234567..HEAD" >/dev/null 2>"$tmp/out" || rc=$?
[ "$rc" = 2 ] || fail "range with an unknown revision: exit $rc (want 2)"
rc=0
scripts/secret-scan.sh range "$base..HEAD" "" >/dev/null 2>&1 || rc=$?
[ "$rc" = 2 ] || fail "range with an empty argument: exit $rc (want 2)"
pass "a range git cannot resolve is an error, not a clean scan"

# ── worktree-only hooks path when another tool owns the clone's ─────────────
git config --local core.hooksPath .husky/_
git worktree add -q "$tmp/wt" -b wt
(cd "$tmp/wt" && scripts/secret-scan.sh install >/dev/null) || fail "worktree install failed"
[ "$(git config --local core.hooksPath)" = .husky/_ ] || fail "the clone-wide hooks path was overwritten"
[ "$(git -C "$tmp/wt" rev-parse --git-path hooks)" = .githooks ] || fail "the worktree does not use .githooks"
pass "an existing clone-wide hooks path is kept; the worktree gets .githooks"

# ── fail closed without gitleaks ───────────────────────────────────────────
nogl="$tmp/bin"
mkdir -p "$nogl"
for tool in git bash sed cat grep tr od head mktemp dirname uname cut; do
  ln -s "$(command -v "$tool")" "$nogl/$tool"
done
rm -rf "$tmp/repo/.git/gitleaks" # the throwaway repository's copy only
rc=0
PATH="$nogl" scripts/secret-scan.sh staged >/dev/null 2>"$tmp/out" || rc=$?
[ "$rc" = 2 ] || fail "missing gitleaks: exit $rc (want 2)"
grep -q "secret-scan.sh install" "$tmp/out" || fail "missing gitleaks: no install hint"
pass "missing gitleaks fails closed with an install hint"

echo "all secret-scan tests passed"
