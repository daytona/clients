#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Tests for stable-v2-release.sh: tag lists in, the first stable 2.x tag out,
# the stable-release query it sends to gh, and a GitHub API failure (stubbed
# gh) is an error rather than "no 2.x".
#
# Usage: bash hack/stable-v2-release.test.sh

set -euo pipefail

script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/stable-v2-release.sh"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

failures=0
expect() {
  local want="$1" tags="$2" got
  got="$(printf '%s\n' "$tags" | bash "$script" --tags-from -)" || {
    echo "FAIL: exited non-zero for tags: $tags"
    failures=$((failures + 1))
    return
  }
  if [ "$got" != "$want" ]; then
    echo "FAIL: want '$want', got '$got' for tags: $tags"
    failures=$((failures + 1))
  fi
}

expect "" ""
expect "" $'v0.227.2\nv0.227.3\nv2.0.0-alpha.7\nv2.0.0-rc.1\nv2.1.0-alpha.1'
expect "" $'sdk-go/v2.0.0\nv2.0\nv2.0.0.1\nv20.0.0-beta.1\nv1.0.0'
expect v2.0.0 $'v0.227.3\nv2.0.0-alpha.7\nv2.0.0\nv0.228.0'
expect v2.0.1 $'v2.1.0\nv2.0.1\nv2.0.1-rc.1'
expect v3.0.0 $'v0.300.0\nv3.0.0'
expect v10.0.0 $'v10.0.0\nv0.1.0'

stub="${work}/bin"
mkdir -p "$stub"
printf '#!/bin/sh\necho "gh: HTTP 502" >&2\nexit 1\n' >"${stub}/gh"
chmod +x "${stub}/gh"
if err="$(PATH="${stub}:$PATH" bash "$script" 2>&1 >/dev/null)"; then
  echo "FAIL: a gh failure must exit non-zero"
  failures=$((failures + 1))
elif ! grep -q "Could not list the releases of daytona/clients" <<<"$err"; then
  echo "FAIL: a gh failure must report that the releases could not be listed, got: $err"
  failures=$((failures + 1))
fi

printf '#!/bin/sh\necho "$*" >"%s"\nprintf "v0.227.3\\nv2.0.0-alpha.7\\nv2.0.0\\n"\n' "${work}/gh-args" >"${stub}/gh"
got="$(PATH="${stub}:$PATH" bash "$script")" || got="exit $?"
if [ "$got" != v2.0.0 ]; then
  echo "FAIL: want v2.0.0 from the GitHub releases, got '$got'"
  failures=$((failures + 1))
fi
for arg in "release list" "--repo daytona/clients" "--limit 1000" --exclude-drafts --exclude-pre-releases "--json tagName" "--jq .\[\].tagName"; do
  if ! grep -q -- "$arg" "${work}/gh-args"; then
    echo "FAIL: gh must be called with $arg, got: $(cat "${work}/gh-args")"
    failures=$((failures + 1))
  fi
done

if [ "$failures" -ne 0 ]; then
  echo "$failures failure(s)"
  exit 1
fi
echo "stable-v2-release: all checks passed"
