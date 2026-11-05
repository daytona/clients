#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Secret scanning with gitleaks, shared by the git hooks in .githooks/ and the
# "Secret scan" workflow. Rules: .gitleaks.toml. See CONTRIBUTING.md.
#
#   scripts/secret-scan.sh install        download the pinned gitleaks (or use
#                                         one on PATH where none is published)
#                                         and enable the hooks in this worktree
#   scripts/secret-scan.sh install-bin    download the pinned gitleaks only
#   scripts/secret-scan.sh staged         scan the staged changes (pre-commit)
#   scripts/secret-scan.sh pre-push URL   scan the commits a push would send;
#                                         reads the pre-push hook's stdin
#   scripts/secret-scan.sh range ARGS...  scan the commits `git log ARGS`
#                                         lists, e.g. `range main..HEAD`
#   scripts/secret-scan.sh ci-push BEFORE AFTER BRANCH
#                                         scan the commits a push added (CI)
#
# Exit status: 0 clean, 1 secrets found, 2 usage or setup error.
#
# Private rules: when <git common dir>/info/gitleaks-private.toml exists, every
# scan also runs it as a second gitleaks config. It is never committed, so it
# can hold patterns that must not be published (internal hostnames, project
# names). It is a complete gitleaks config file.
set -euo pipefail

GITLEAKS_VERSION=8.30.1

# sha256 of the release archives, from gitleaks_${GITLEAKS_VERSION}_checksums.txt
# in the GitHub release. Update together with GITLEAKS_VERSION.
gitleaks_sha256() {
  case "$1" in
    linux_x64) echo 551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb ;;
    linux_arm64) echo e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080 ;;
    darwin_x64) echo dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709 ;;
    darwin_arm64) echo b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5 ;;
    *) return 1 ;;
  esac
}

die() { echo "secret-scan: $*" >&2; exit 2; }

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || die "not inside a git working tree"
common_dir=$(cd "$repo_root" && cd "$(git rev-parse --git-common-dir)" && pwd)
bin_dir="$common_dir/gitleaks/$GITLEAKS_VERSION"
private_config="$common_dir/info/gitleaks-private.toml"
config="$repo_root/.gitleaks.toml"

platform() {
  local os arch
  case "$(uname -s)" in
    Linux) os=linux ;;
    Darwin) os=darwin ;;
    *) return 1 ;;
  esac
  case "$(uname -m)" in
    x86_64 | amd64) arch=x64 ;;
    aarch64 | arm64) arch=arm64 ;;
    *) return 1 ;;
  esac
  echo "${os}_${arch}"
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

install_bin() {
  if [ -x "$bin_dir/gitleaks" ]; then
    echo "secret-scan: gitleaks $GITLEAKS_VERSION already installed at $bin_dir/gitleaks"
    return 0
  fi
  local plat want got archive url
  plat=$(platform) || die "no pinned gitleaks build for $(uname -s)/$(uname -m); install gitleaks $GITLEAKS_VERSION on PATH (https://github.com/gitleaks/gitleaks#installing)"
  want=$(gitleaks_sha256 "$plat")
  command -v curl >/dev/null 2>&1 || die "curl is required to download gitleaks"
  archive="gitleaks_${GITLEAKS_VERSION}_${plat}.tar.gz"
  url="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/${archive}"
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL --retry 3 -o "$tmp/$archive" "$url" || die "download failed: $url"
  got=$(sha256_of "$tmp/$archive")
  [ "$got" = "$want" ] || die "checksum mismatch for $archive: got $got, want $want"
  tar -xzf "$tmp/$archive" -C "$tmp" gitleaks
  mkdir -p "$bin_dir"
  chmod 0755 "$tmp/gitleaks"
  # Same filesystem as the final path, so the rename is atomic for a
  # concurrent install from another worktree.
  mv "$tmp/gitleaks" "$bin_dir/gitleaks.$$"
  mv -f "$bin_dir/gitleaks.$$" "$bin_dir/gitleaks"
  echo "secret-scan: installed gitleaks $GITLEAKS_VERSION at $bin_dir/gitleaks"
}

# Hooks are always enabled per worktree: a clone-wide core.hooksPath is what
# other tools write (husky's `postinstall` on the 0.x line sets .husky/_ for
# the whole clone), and a later clone-wide write must not switch the scan off.
install_hooks() {
  local previous
  previous=$(git config --get core.hooksPath || true)
  enable_worktree_config
  git config --worktree core.hooksPath .githooks
  if [ -n "$previous" ] && [ "$previous" != .githooks ]; then
    # The replaced path keeps running behind the scan (see chained_hook).
    git config --worktree secretscan.chainHooksPath "$previous"
    echo "secret-scan: core.hooksPath was '$previous'; its hooks still run alongside the scan"
  fi
  [ "$(git -C "$repo_root" rev-parse --git-path hooks)" = .githooks ] \
    || die "core.hooksPath did not take effect (git rev-parse --git-path hooks: $(git -C "$repo_root" rev-parse --git-path hooks))"
  echo "secret-scan: hooks enabled for this worktree (.githooks/pre-commit, .githooks/pre-push); other worktrees run install too"
}

# enable_worktree_config: turn on extensions.worktreeConfig the way
# git-worktree(1) prescribes. Once it is on, every linked worktree reads the
# shared config, so core.bare and core.worktree, which describe only the main
# worktree, move from it into the main worktree's own config.worktree. Left in
# place, a bare clone's core.bare=true breaks every linked worktree.
enable_worktree_config() {
  local shared="$common_dir/config" main="$common_dir/config.worktree" key value
  for key in core.bare core.worktree; do
    value=$(git config --file "$shared" --get "$key" || true)
    [ -n "$value" ] || continue
    git config --file "$main" --get "$key" >/dev/null || git config --file "$main" "$key" "$value"
    git config --file "$shared" --unset "$key"
  done
  git config --file "$shared" extensions.worktreeConfig true
}

# chained_hook NAME: print the hook the .githooks wrapper runs besides the
# scan: NAME from the hooks path install replaced, else from <common dir>/hooks.
# A hook that is itself a wrapper (it calls chained-hook: a symlink to this
# one, or a copy of any version from any worktree) would chain to itself
# forever, so it is not chained.
chained_hook() {
  local dir hook
  dir=$(git config --get secretscan.chainHooksPath || true)
  [ -n "$dir" ] || dir="$common_dir/hooks"
  case "$dir" in /*) ;; *) dir="$repo_root/$dir" ;; esac
  hook="$dir/$1"
  [ -x "$hook" ] || return 0
  [ "$(cd "$dir" && pwd -P)" != "$(cd "$repo_root/.githooks" && pwd -P)" ] || return 0
  grep -qF chained-hook "$hook" || echo "$hook"
}

# install: the pinned binary where one is published, else a gitleaks on PATH.
install_all() {
  if platform >/dev/null; then
    install_bin
  elif command -v gitleaks >/dev/null 2>&1; then
    echo "secret-scan: no pinned gitleaks build for $(uname -s)/$(uname -m); using $(command -v gitleaks)"
    resolve_gitleaks >/dev/null
  else
    die "no pinned gitleaks build for $(uname -s)/$(uname -m); install gitleaks $GITLEAKS_VERSION on PATH (https://github.com/gitleaks/gitleaks#installing), then re-run install"
  fi
  install_hooks
}

# Prefer the pinned binary, then gitleaks on PATH.
resolve_gitleaks() {
  if [ -x "$bin_dir/gitleaks" ]; then
    echo "$bin_dir/gitleaks"
  elif command -v gitleaks >/dev/null 2>&1; then
    local v
    v=$(gitleaks version 2>/dev/null || true)
    [ "$v" = "$GITLEAKS_VERSION" ] || [ "$v" = "v$GITLEAKS_VERSION" ] \
      || echo "secret-scan: warning: gitleaks on PATH is '$v', this repository pins $GITLEAKS_VERSION" >&2
    command -v gitleaks
  else
    die "gitleaks is not installed. Run: scripts/secret-scan.sh install"
  fi
}

# run_gitleaks ARGS...: run once per config, report every finding, fail if any.
run_gitleaks() {
  local gl status=0
  gl=$(resolve_gitleaks)
  [ -f "$config" ] || die "missing $config"
  "$gl" git "$@" --config "$config" --redact --no-banner --verbose --log-level warn "$repo_root" || status=$?
  if [ -f "$private_config" ]; then
    local private_status=0
    # Inline gitleaks:allow does not apply: a private pattern never belongs in
    # this repository, whatever the line says.
    "$gl" git "$@" --config "$private_config" --ignore-gitleaks-allow --redact --no-banner --verbose --log-level warn "$repo_root" || private_status=$?
    [ "$private_status" -eq 0 ] || status=$private_status
  fi
  if [ "$status" -ne 0 ]; then
    cat >&2 <<'EOF'

secret-scan: BLOCKED — the scan above found a secret or a private pattern.
  - Remove it from the change (use an environment variable or a placeholder).
  - If it was ever pushed or shared, treat it as leaked and rotate it.
  - Not a secret (a fixture or a documented example)? Add a `gitleaks:allow`
    comment on that line, or an exact-value [[allowlists]] entry in
    .gitleaks.toml that says why it is safe. Private rules
    (.git/info/gitleaks-private.toml) have no inline exception.
EOF
    exit 1
  fi
}

# scan_log GIT_LOG_ARGS...: scan the commits `git log GIT_LOG_ARGS` lists.
# gitleaks reports "no leaks" when git rejects its --log-opts (an unknown
# revision, a commit missing from a shallow clone) or gets an empty argument,
# so the revisions are checked here first and a bad range is an error.
scan_log() {
  local arg
  [ $# -gt 0 ] || die "no revisions to scan"
  for arg in "$@"; do
    case "$arg" in
      "" | *[[:space:]]*) die "revision arguments must be non-empty and contain no whitespace: '$arg'" ;;
    esac
  done
  git rev-list "$@" >/dev/null || die "git rejected the revisions to scan: $*"
  # gitleaks reads `git log -p`, which shows no diff for a merge commit, so a
  # secret added while resolving a merge would pass. --remerge-diff (git
  # 2.36+) shows what the merge changed against git's own merge of its parents.
  git log -n1 --remerge-diff --format= "$1" >/dev/null 2>&1 \
    || die "git $(git version | cut -d' ' -f3) cannot scan merge commits (--remerge-diff); git 2.36 or later is required"
  run_gitleaks --log-opts="--remerge-diff $*"
}

is_zero() { [ -z "${1//0/}" ]; }

# pre-push: stdin carries "<local ref> <local sha> <remote ref> <remote sha>"
# per pushed ref. Scan the commits the remote does not have yet. What the
# remote has is asked from the remote itself (ls-remote), never read from the
# local remote-tracking refs: those go stale when a branch is deleted or
# rewritten elsewhere, and excluding a stale tip would skip its commits.
scan_pre_push() {
  local remote="$1" revs=() _lref local_sha _rref remote_sha advertised sha _ref
  while read -r _lref local_sha _rref remote_sha; do
    [ -n "${local_sha:-}" ] || continue
    is_zero "$local_sha" && continue # deleting a remote ref sends nothing
    revs+=("$local_sha")
    if ! is_zero "$remote_sha" && git cat-file -e "${remote_sha}^{commit}" 2>/dev/null; then
      revs+=("^$remote_sha")
    fi
  done
  [ "${#revs[@]}" -gt 0 ] || exit 0
  if advertised=$(git ls-remote --heads --tags --refs -- "$remote" 2>/dev/null); then
    while read -r sha _ref; do
      [ -n "${sha:-}" ] || continue
      git cat-file -e "${sha}^{commit}" 2>/dev/null && revs+=("^$sha")
    done <<<"$advertised"
  else
    echo "secret-scan: could not list the remote's refs; scanning every commit being pushed" >&2
  fi
  scan_log "${revs[@]}"
}

# ci-push BEFORE AFTER BRANCH: the commits a push to BRANCH of the origin
# checkout added. With a known previous tip, BEFORE..AFTER (also right after a
# force push: it lists every rewritten commit). Without one (a new branch, or
# a previous tip CI cannot see), everything AFTER reaches that no other
# branch of origin has.
scan_ci_push() {
  local before="$1" after="$2" branch="$3"
  if [ -n "$before" ] && ! is_zero "$before" && git cat-file -e "${before}^{commit}" 2>/dev/null; then
    scan_log "$before..$after"
  else
    scan_log "$after" --not "--exclude=origin/$branch" --remotes=origin
  fi
}

cmd="${1:-}"
[ $# -gt 0 ] && shift
case "$cmd" in
  install) install_all ;;
  install-bin) install_bin ;;
  chained-hook)
    [ $# -eq 1 ] || die "usage: secret-scan.sh chained-hook HOOK_NAME"
    chained_hook "$1"
    ;;
  staged) run_gitleaks --pre-commit --staged ;;
  pre-push)
    [ $# -ge 1 ] || die "usage: secret-scan.sh pre-push REMOTE_URL < hook-stdin"
    scan_pre_push "$1"
    ;;
  ci-push)
    [ $# -eq 3 ] || die "usage: secret-scan.sh ci-push BEFORE AFTER BRANCH"
    scan_ci_push "$1" "$2" "$3"
    ;;
  range)
    [ $# -ge 1 ] || die "usage: secret-scan.sh range GIT_LOG_ARGS..."
    scan_log "$@"
    ;;
  *)
    sed -n '5,25p' "$0" | sed 's/^# \{0,1\}//' >&2
    exit 2
    ;;
esac
