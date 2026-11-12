#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Tests for npm-publish.sh with npm and curl stubbed on PATH.
#
# Usage: bash hack/npm-publish.test.sh   (needs node)

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
publish="${script_dir}/npm-publish.sh"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

mkdir -p "${work}/bin" "${work}/pkg"
echo '{"name":"@daytona/api-client","version":"0.231.0"}' >"${work}/pkg/package.json"
# curl prints the registry status from $REGISTRY_STATUS and logs the URL.
# shellcheck disable=SC2016 # expanded by the stub, not here
printf '#!/bin/sh\nfor a; do url="$a"; done\necho "$url" >"$CURL_LOG"\nprintf %%s "$REGISTRY_STATUS"\n' >"${work}/bin/curl"
# npm logs each call to $NPM_LOG and exits with $NPM_STATUS.
# shellcheck disable=SC2016 # expanded by the stub, not here
printf '#!/bin/sh\necho "$*" >>"$NPM_LOG"\nexit "$NPM_STATUS"\n' >"${work}/bin/npm"
chmod +x "${work}/bin/"*

failures=0
# expect <pass|fail> <description> <registry status> <npm status> <expected npm calls> <expected output>
expect() {
  local want="$1" desc="$2" registry="$3" npm_status="$4" calls="$5" match="$6" out status=0 made
  : >"${work}/npm.log"
  out="$(cd "${work}/pkg" && PATH="${work}/bin:$PATH" NPM_TAG=latest REGISTRY_STATUS="$registry" \
    NPM_STATUS="$npm_status" NPM_LOG="${work}/npm.log" CURL_LOG="${work}/curl.log" bash "$publish" 2>&1)" || status=$?
  made=$(wc -l <"${work}/npm.log")
  if { [ "$want" = pass ] && [ "$status" -ne 0 ]; } || { [ "$want" = fail ] && [ "$status" -eq 0 ]; }; then
    echo "FAIL: $desc (exit $status)"$'\n'"$out"
    failures=$((failures + 1))
  elif [ "$made" -ne "$calls" ]; then
    echo "FAIL: $desc: $made npm call(s), expected $calls"$'\n'"$out"
    failures=$((failures + 1))
  elif ! grep -qF -- "$match" <<<"$out$(cat "${work}/npm.log")"; then
    echo "FAIL: $desc: output lacks '$match'"$'\n'"$out"
    failures=$((failures + 1))
  else
    echo "ok: $desc"
  fi
}

expect pass "publishes a version missing from npm" 404 0 1 \
  "publish --tag latest --access public --registry https://registry.npmjs.org/"
expect pass "skips a version already on npm" 200 0 0 "@daytona/api-client@0.231.0 is already on npm, skipping"
expect fail "fails closed when the registry errors" 503 0 0 "registry answered HTTP 503"
expect fail "fails closed when the registry is unreachable" "" 0 0 "registry answered HTTP 000"
expect fail "a failed publish fails the step" 404 1 1 "publish --tag latest"
if grep -qF "https://registry.npmjs.org/@daytona%2fapi-client/0.231.0?nocache=" "${work}/curl.log"; then
  echo "ok: looks up the scoped package version"
else
  echo "FAIL: unexpected registry URL: $(cat "${work}/curl.log")"
  failures=$((failures + 1))
fi

if [ "$failures" -ne 0 ]; then
  echo "$failures failure(s)"
  exit 1
fi
echo "npm-publish: all checks passed"
