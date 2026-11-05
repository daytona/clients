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
# Seed the pinned-binary cache so install does not download again.
mkdir -p ".git/gitleaks/$version"
cp "$(command -v gitleaks)" ".git/gitleaks/$version/gitleaks"
scripts/secret-scan.sh install >"$tmp/out" 2>&1 || fail "install failed: $(cat "$tmp/out")"
[ "$(git config --worktree core.hooksPath)" = .githooks ] || fail "install did not set core.hooksPath for the worktree"
[ -z "$(git config --file .git/config --get core.hooksPath || true)" ] || fail "install set a clone-wide core.hooksPath"
[ "$(git rev-parse --is-bare-repository)" = false ] || fail "core.bare was lost"
(cd scripts && ./secret-scan.sh install >/dev/null) || fail "install from a subdirectory failed"
pass "install sets core.hooksPath=.githooks per worktree, also from a subdirectory"

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
pass "the clone's own pre-commit hook still runs"

# A chained hook runs before the scan, so what it stages is scanned.
printf '#!/bin/sh\necho "DAYTONA_API_KEY=%s" >generated.env\ngit add generated.env\n' "$daytona_key" >.git/hooks/pre-commit
echo two >chain.txt
git add chain.txt
if git commit -q -m "chain stages a secret" >/dev/null 2>&1; then fail "a secret staged by a chained hook was not blocked"; fi
rm .git/hooks/pre-commit generated.env
git rm -q --cached generated.env
pass "a secret staged by the chained hook is blocked"

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

# The remote's refs come from where the push goes ($2), not its fetch url.
git init -q --bare "$tmp/mirror.git"
git --git-dir="$tmp/mirror.git" fetch -q "$tmp/repo" main:refs/heads/main
git remote add split "$tmp/mirror.git"
git remote set-url --push split "$tmp/pushtarget.git"
git init -q --bare "$tmp/pushtarget.git"
if git push -q split main >/dev/null 2>&1; then fail "refs at the fetch url exempted commits pushed to a different pushurl"; fi
git remote remove split
pass "the push target's refs decide what is scanned (pushurl)"

# A branch deleted on the remote by someone else leaves a stale tracking ref
# here; its commits are no longer on the remote and must be scanned again.
git push -q --no-verify origin main:refs/heads/gone
git --git-dir="$tmp/remote.git" update-ref -d refs/heads/gone
git rev-parse -q --verify refs/remotes/origin/gone >/dev/null || fail "fixture: no stale tracking ref"
if git push -q origin main:refs/heads/again >/dev/null 2>&1; then fail "a stale remote-tracking ref exempted a secret from the push scan"; fi
pass "a stale remote-tracking ref does not exempt its commits"

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

# A secret added while resolving a merge exists only in the merge commit.
git init -q -b main "$tmp/merge"
git init -q --bare "$tmp/merge-remote.git"
(
  cd "$tmp/merge"
  mkdir -p scripts
  cp "$src/.gitleaks.toml" .
  cp -R "$src/.githooks" .
  cp "$src/scripts/secret-scan.sh" scripts/
  mkdir -p ".git/gitleaks/$version"
  cp "$(command -v gitleaks)" ".git/gitleaks/$version/gitleaks"
  scripts/secret-scan.sh install >/dev/null
  git remote add origin "$tmp/merge-remote.git"
  git add -A && git commit -q -m tooling && git push -q origin main
  git checkout -q -b feat && echo feat >feat.txt && git add feat.txt && git commit -q -m feat
  git checkout -q main && echo main >main.txt && git add main.txt && git commit -q -m main
  git merge -q --no-commit feat >/dev/null 2>&1
  echo "DAYTONA_API_KEY=$daytona_key" >merged.env
  git add merged.env
  git commit -q --no-verify -m "evil merge"
  if scripts/secret-scan.sh range origin/main..main >/dev/null 2>&1; then fail "range missed a secret only in a merge commit"; fi
  if git push -q origin main >/dev/null 2>&1; then fail "push missed a secret only in a merge commit"; fi
) || exit 1
pass "a secret that exists only in a merge commit is caught by range and pre-push"

# ci-push: the workflow's push scan. The secret sits below a clean tip.
echo clean >after-leak.txt
git add after-leak.txt
git commit -q --no-verify -m "clean tip"
zero=0000000000000000000000000000000000000000
side=$(git commit-tree -p "$base" -m side "$base^{tree}")
if scripts/secret-scan.sh ci-push "$side" HEAD main >/dev/null 2>&1; then fail "ci-push after a force push missed a secret below the tip"; fi
if scripts/secret-scan.sh ci-push "$zero" HEAD fresh >/dev/null 2>&1; then fail "ci-push of a new branch missed a secret below the tip"; fi
git update-ref refs/remotes/origin/fresh HEAD # what the CI checkout fetches
if scripts/secret-scan.sh ci-push "$zero" HEAD fresh >/dev/null 2>&1; then fail "ci-push excluded the pushed branch's own commits"; fi
git update-ref -d refs/remotes/origin/fresh
scripts/secret-scan.sh ci-push "$zero" "$base" fresh >/dev/null 2>&1 || fail "ci-push of a new branch rescanned history other branches have"
scripts/secret-scan.sh ci-push "$base~1" "$base" main >/dev/null 2>&1 || fail "ci-push of a clean fast-forward was blocked"
pass "ci-push scans every new commit after a force push or a new branch"

# ── worktree-only hooks path when another tool owns the clone's ─────────────
git config --local core.hooksPath .husky/_
# What husky's postinstall does in another worktree: a clone-wide write.
[ "$(git rev-parse --git-path hooks)" = .githooks ] || fail "a later clone-wide core.hooksPath switched the hooks off"
echo "DAYTONA_API_KEY=$daytona_key" >late.env
git add late.env
if git commit -q -m "after husky" >/dev/null 2>&1; then fail "a later clone-wide core.hooksPath let a secret through"; fi
git rm -q --cached late.env
rm late.env
pass "a later clone-wide core.hooksPath (husky) does not switch the hooks off"

# husky's hooks in that later clone-wide path still run (lint-staged).
mkdir -p .husky/_
printf '#!/bin/sh\ntouch "%s/late-husky-ran"\n' "$tmp" >.husky/_/pre-commit
chmod +x .husky/_/pre-commit
echo late >late.txt
git add late.txt
git commit -q -m "late husky" || fail "commit with a later clone-wide hooks path failed"
[ -f "$tmp/late-husky-ran" ] || fail "a clone-wide hooks path set after install was not chained"
rm -rf .husky
pass "a clone-wide hooks path set after install (husky) still runs"

git worktree add -q "$tmp/wt" -b wt
# git 2.37+ copies the main worktree's config.worktree into a new worktree.
IFS=. read -r git_major git_minor _ <<<"$(git version | cut -d' ' -f3)"
if [ "$git_major" -gt 2 ] || { [ "$git_major" -eq 2 ] && [ "$git_minor" -ge 37 ]; }; then
  [ "$(git -C "$tmp/wt" rev-parse --git-path hooks)" = .githooks ] || fail "a new worktree did not inherit the hooks"
  pass "a worktree added after install inherits the hooks"
fi
git -C "$tmp/wt" config --worktree --unset core.hooksPath || true # a worktree with no override of its own
(cd "$tmp/wt" && scripts/secret-scan.sh install >/dev/null) || fail "worktree install failed"
[ "$(git config --local core.hooksPath)" = .husky/_ ] || fail "the clone-wide hooks path was overwritten"
[ "$(git -C "$tmp/wt" rev-parse --git-path hooks)" = .githooks ] || fail "the worktree does not use .githooks"
mkdir -p "$tmp/wt/.husky/_"
printf '#!/bin/sh\necho x >>"%s/husky-ran"\n' "$tmp" >"$tmp/wt/.husky/_/pre-commit"
chmod +x "$tmp/wt/.husky/_/pre-commit"
echo wt >"$tmp/wt/wt.txt"
git -C "$tmp/wt" add wt.txt
git -C "$tmp/wt" commit -q -m wt || fail "commit in the worktree failed"
[ -f "$tmp/husky-ran" ] || fail "the replaced hooks path's pre-commit did not run"
[ "$(wc -l <"$tmp/husky-ran")" -eq 1 ] || fail "a path that is both the replaced and the clone-wide one ran twice"
pass "an existing clone-wide hooks path is kept; the worktree gets .githooks and still runs it once"

# A worktree whose replaced path differs from a clone-wide one set later
# (husky after install): both run.
mkdir -p "$tmp/wt/own-hooks"
printf '#!/bin/sh\ntouch "%s/own-ran"\n' "$tmp" >"$tmp/wt/own-hooks/pre-commit"
chmod +x "$tmp/wt/own-hooks/pre-commit"
git -C "$tmp/wt" config --worktree secretscan.chainHooksPath own-hooks
rm -f "$tmp/husky-ran"
echo both >"$tmp/wt/wt.txt"
git -C "$tmp/wt" add wt.txt
git -C "$tmp/wt" commit -q -m both || fail "commit with two chained paths failed"
[ -f "$tmp/own-ran" ] || fail "the replaced hooks path did not run next to a clone-wide one"
[ -f "$tmp/husky-ran" ] || fail "a clone-wide hooks path was masked by the replaced one"
git -C "$tmp/wt" config --worktree --unset secretscan.chainHooksPath
rm -rf "$tmp/wt/own-hooks"
pass "the replaced hooks path and a later clone-wide one both run"

git config --local --unset core.hooksPath
git worktree add -q "$tmp/wt2" -b wt2
git -C "$tmp/wt2" config --worktree core.hooksPath .husky/_
(cd "$tmp/wt2" && scripts/secret-scan.sh install >/dev/null) || fail "install over a worktree override failed"
[ "$(git -C "$tmp/wt2" rev-parse --git-path hooks)" = .githooks ] || fail "the worktree override was not replaced"
[ -z "$(git config --local --get core.hooksPath || true)" ] || fail "the clone-wide hooks path was set"
pass "an existing worktree override is replaced, the clone's setting untouched"

# A bare clone with linked worktrees: core.bare must not reach them.
git clone -q --bare "$tmp/repo" "$tmp/bare.git"
git --git-dir="$tmp/bare.git" config core.hooksPath .husky/_
git --git-dir="$tmp/bare.git" worktree add -q "$tmp/bwt" -b bwt main
mkdir -p "$tmp/bare.git/gitleaks/$version"
cp "$(command -v gitleaks)" "$tmp/bare.git/gitleaks/$version/gitleaks"
(cd "$tmp/bwt" && scripts/secret-scan.sh install >/dev/null) || fail "install in a bare clone's worktree failed"
git -C "$tmp/bwt" status --short >/dev/null 2>&1 || fail "install broke the bare clone's worktree"
[ "$(git --git-dir="$tmp/bare.git" rev-parse --is-bare-repository)" = true ] || fail "the bare clone is no longer bare"
git --git-dir="$tmp/bare.git" worktree add -q "$tmp/bwt2" -b bwt2 main
git -C "$tmp/bwt2" status --short >/dev/null 2>&1 || fail "a worktree added after install is broken"
echo "DAYTONA_API_KEY=$daytona_key" >"$tmp/bwt/leak.env"
git -C "$tmp/bwt" add leak.env
if git -C "$tmp/bwt" commit -q -m leak >/dev/null 2>&1; then fail "the bare clone's worktree did not block a secret"; fi
pass "a bare clone's worktrees keep working and scanning after install"

ln -s .githooks "$tmp/wt2/hooks-link"
git -C "$tmp/wt2" config --worktree secretscan.chainHooksPath hooks-link
[ -z "$(cd "$tmp/wt2" && scripts/secret-scan.sh chained-hook pre-commit)" ] || fail "a chained path symlinked to .githooks chains the wrapper to itself"
pass "a chained hooks path that is a symlink to .githooks is not chained to itself"

# No pinned build for the platform: install uses gitleaks from PATH.
shim="$tmp/shim"
mkdir -p "$shim"
cat >"$shim/uname" <<'EOF'
#!/bin/sh
case "$1" in -m) echo x86_64 ;; *) echo FreeBSD ;; esac
EOF
chmod +x "$shim/uname"
git init -q "$tmp/unsup"
mkdir -p "$tmp/unsup/scripts"
cp "$src/.gitleaks.toml" "$tmp/unsup/"
cp "$src/scripts/secret-scan.sh" "$tmp/unsup/scripts/"
cp -R "$src/.githooks" "$tmp/unsup/"
(cd "$tmp/unsup" && PATH="$shim:$PATH" scripts/secret-scan.sh install >/dev/null) || fail "install with gitleaks on PATH failed on an unpublished platform"
[ "$(git -C "$tmp/unsup" rev-parse --git-path hooks)" = .githooks ] || fail "hooks not enabled on an unpublished platform"
echo "DAYTONA_API_KEY=$daytona_key" >"$tmp/unsup/leak.env"
git -C "$tmp/unsup" add leak.env
if PATH="$shim:$PATH" git -C "$tmp/unsup" commit -q -m leak >/dev/null 2>&1; then fail "the PATH gitleaks did not block on an unpublished platform"; fi
pass "an unpublished platform installs with gitleaks from PATH"

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
