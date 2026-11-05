#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Dry run of the "Update Homebrew CLI tap" step of sdk_publish.yaml, extracted
# from the workflow and run against stubbed gh, curl and sleep: events are
# dispatched in order, each waits for its tap run, and update-version is
# skipped when a stable 2.x release appears while the daytona@0 run is in
# flight.
#
# Usage: bash hack/homebrew-tap-dispatch.test.sh

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# The step's `run: |` block, with its YAML indentation removed.
awk '
  /^      - name: Update Homebrew CLI tap$/ { in_step = 1; next }
  in_step && /^      - name: / { exit }
  in_step && /^        run: \|$/ { in_run = 1; next }
  in_run && /^          / { print substr($0, 11); next }
  in_run && /^$/ { print; next }
  in_run { exit }
' "${repo_root}/.github/workflows/sdk_publish.yaml" >"${work}/step.sh"
grep -q 'hack/stable-v2-release.sh' "${work}/step.sh" || {
  echo "FAIL: could not extract the Update Homebrew CLI tap step"
  exit 1
}

failures=0
fail() {
  echo "FAIL: $*"
  failures=$((failures + 1))
}

# run_step <case> <stable tag after the first dispatch> <release list fails
# after the first dispatch> [tap run: success (default), failure, slow (in
# progress for two polls, then success) or stuck (never completes)]
run_step() {
  local dir="${work}/$1" rc=0
  mkdir -p "${dir}/bin"
  : >"${dir}/sent"
  : >"${dir}/polls"
  cat >"${dir}/bin/curl" <<EOF
#!/usr/bin/env bash
for arg; do
  case "\$arg" in *event_type*) echo "\$arg" | sed -E 's/.*"event_type": "([^"]+)".*/\1/' >>"${dir}/sent" ;; esac
done
EOF
  cat >"${dir}/bin/gh" <<EOF
#!/usr/bin/env bash
dispatched=\$(wc -l <"${dir}/sent")
case "\$*" in
  *"/runs?per_page=1"*) echo \$((100 * dispatched)) ;;
  *"/runs?event=repository_dispatch"*)
    echo >>"${dir}/polls"
    url="https://example.invalid/run/\$((100 * dispatched))"
    case "${4:-success}" in
      success) echo "completed success \$url" ;;
      failure) echo "completed failure \$url" ;;
      slow) if [ "\$(wc -l <"${dir}/polls")" -le 2 ]; then echo "in_progress null \$url"; else echo "completed success \$url"; fi ;;
      stuck) echo "queued null \$url" ;;
    esac ;;
  "release list"*)
    if [ "$3" = fail ] && [ "\$dispatched" -gt 0 ]; then echo "HTTP 502" >&2; exit 1; fi
    echo v0.227.3
    echo v2.0.0-alpha.7
    if [ -n "$2" ] && [ "\$dispatched" -gt 0 ]; then echo "$2"; fi ;;
  *) echo "unexpected gh \$*" >&2; exit 1 ;;
esac
EOF
  printf '#!/bin/sh\nexit 0\n' >"${dir}/bin/sleep"
  chmod +x "${dir}/bin/"*
  (
    cd "$repo_root"
    PATH="${dir}/bin:$PATH" GITHUB_REF=refs/heads/release/v0 GITHUB_REPOSITORY=daytona/clients \
      VERSION=v0.300.0 GITHUBBOT_TOKEN=unused HOMEBREW_EVENTS=$'update-version-v0\nupdate-version' \
      bash -e "${work}/step.sh"
  ) >"${dir}/out" 2>&1 || rc=$?
  echo "$rc" >"${dir}/rc"
}

sent() { paste -sd, "${work}/$1/sent"; }
polls() { wc -l <"${work}/$1/polls" | tr -d ' '; }
rc() { cat "${work}/$1/rc"; }

run_step steady "" ""
[ "$(rc steady)" = 0 ] || fail "steady: exit $(rc steady): $(cat "${work}/steady/out")"
[ "$(sent steady)" = update-version-v0,update-version ] || fail "steady: dispatched '$(sent steady)'"

run_step ga v2.0.0 ""
[ "$(rc ga)" = 0 ] || fail "ga: exit $(rc ga): $(cat "${work}/ga/out")"
[ "$(sent ga)" = update-version-v0 ] || fail "ga: a stable 2.x published mid-run must skip update-version, dispatched '$(sent ga)'"
grep -q "Stable 2.x release v2.0.0 was published during this run" "${work}/ga/out" || fail "ga: skip not logged: $(cat "${work}/ga/out")"

run_step prerelease v2.1.0-rc.1 ""
[ "$(sent prerelease)" = update-version-v0,update-version ] || fail "prerelease: a 2.x prerelease must not skip update-version, dispatched '$(sent prerelease)'"

run_step api-error "" fail
[ "$(rc api-error)" != 0 ] || fail "api-error: a failed recheck must fail the step"
[ "$(sent api-error)" = update-version-v0 ] || fail "api-error: a failed recheck must not dispatch update-version, dispatched '$(sent api-error)'"

run_step slow "" "" slow
[ "$(rc slow)" = 0 ] || fail "slow: exit $(rc slow): $(cat "${work}/slow/out")"
[ "$(sent slow)" = update-version-v0,update-version ] || fail "slow: dispatched '$(sent slow)'"
[ "$(polls slow)" = 4 ] || fail "slow: want 3 polls for the in-progress run and 1 for the next, got $(polls slow)"

run_step tap-failure "" "" failure
[ "$(rc tap-failure)" != 0 ] || fail "tap-failure: a failed tap run must fail the step"
[ "$(sent tap-failure)" = update-version-v0 ] || fail "tap-failure: nothing may follow a failed tap run, dispatched '$(sent tap-failure)'"
grep -q "ended with failure" "${work}/tap-failure/out" || fail "tap-failure: not reported: $(cat "${work}/tap-failure/out")"

run_step stuck "" "" stuck
[ "$(rc stuck)" != 0 ] || fail "stuck: a tap run that never completes must fail the step"
[ "$(sent stuck)" = update-version-v0 ] || fail "stuck: nothing may follow an unfinished tap run, dispatched '$(sent stuck)'"
[ "$(polls stuck)" = 90 ] || fail "stuck: want 90 polls before giving up, got $(polls stuck)"
grep -q "did not finish within 15 minutes" "${work}/stuck/out" || fail "stuck: not reported: $(cat "${work}/stuck/out")"

if [ "$failures" -ne 0 ]; then
  echo "$failures failure(s)"
  exit 1
fi
echo "homebrew-tap-dispatch: all checks passed"
