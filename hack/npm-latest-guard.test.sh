#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Tests for npm-latest-guard.sh against a stubbed npm registry (npm and sleep
# on PATH). Covers the state after the 2.x SDK is published as `latest`: 0.x
# publishes from release/v0 must never take `latest` back.
#
# Usage: bash hack/npm-latest-guard.test.sh   (needs node)

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
guard="${script_dir}/npm-latest-guard.sh"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

mkdir -p "${work}/bin" "${work}/registry"
cat >"${work}/bin/npm" <<'EOF'
#!/usr/bin/env bash
# npm view <pkg> versions --json | npm view <pkg> dist-tags.latest
[ "$1" = view ] || { echo "unexpected npm call: $*" >&2; exit 1; }
file="${FAKE_REGISTRY}/$(echo "$2" | tr '/@' '__')"
case "$3" in
  versions) cat "${file}.versions" ;;
  dist-tags.latest) cat "${file}.latest" ;;
  *) echo "unexpected npm call: $*" >&2; exit 1 ;;
esac
EOF
printf '#!/bin/sh\nexit 0\n' >"${work}/bin/sleep"
chmod +x "${work}/bin/npm" "${work}/bin/sleep"

# publish <pkg> <latest> <version>...
publish() {
  local pkg="$1" latest="$2" file
  shift 2
  file="${work}/registry/$(echo "$pkg" | tr '/@' '__')"
  node -e 'console.log(JSON.stringify(process.argv.slice(1)))' "$@" >"${file}.versions"
  echo "$latest" >"${file}.latest"
}

failures=0
# expect <pass|fail> <description> <expected output or ""> <guard args...>
expect() {
  local want="$1" desc="$2" match="$3" out status=0
  shift 3
  if [ -n "$PACKAGES" ]; then
    out="$(PATH="${work}/bin:$PATH" FAKE_REGISTRY="${work}/registry" PACKAGES="$PACKAGES" bash "$guard" "$@" 2>&1)" || status=$?
  else
    out="$(env -u PACKAGES PATH="${work}/bin:$PATH" FAKE_REGISTRY="${work}/registry" bash "$guard" "$@" 2>&1)" || status=$?
  fi
  if { [ "$want" = pass ] && [ "$status" -ne 0 ]; } || { [ "$want" = fail ] && [ "$status" -eq 0 ]; }; then
    echo "FAIL: $desc (exit $status)"$'\n'"$out"
    failures=$((failures + 1))
  elif [ -n "$match" ] && ! grep -qF -- "$match" <<<"$out"; then
    echo "FAIL: $desc: output lacks '$match'"$'\n'"$out"
    failures=$((failures + 1))
  else
    echo "ok: $desc"
  fi
}

# The guard's default package list (PACKAGES unset): every package the
# sdk-typescript publish target releases.
PACKAGES=""
all_packages=(@daytona/sdk @daytona/api-client @daytona/toolbox-api-client @daytona/analytics-api-client
  @daytonaio/sdk @daytonaio/api-client @daytonaio/toolbox-api-client)

# Every package still on 0.x: a 0.x release above the last one may take latest.
for pkg in "${all_packages[@]}"; do publish "$pkg" 0.227.0 0.226.0 0.227.0; done
expect pass "0.x may take latest while every package is on 0.x" "" check 0.228.0

# After the cutover each package in turn has a stable 2.0.0 holding latest; the
# guard must refuse a 0.x latest for that package and accept verify.
for pkg in "${all_packages[@]}"; do
  for other in "${all_packages[@]}"; do publish "$other" 0.227.0 0.226.0 0.227.0; done
  publish "$pkg" 2.0.0 0.226.0 0.227.0 2.0.0-alpha.4 2.0.0-rc.1 2.0.0
  expect fail "$pkg: 0.x patch cannot take latest once 2.0.0 is published" \
    "$pkg@2.0.0 is already published; 0.228.0 with --tag latest would move latest backwards. Use a line tag such as v0-latest." \
    check 0.228.0
  expect fail "$pkg: a 0.x release far above the last one still cannot take latest" "$pkg@2.0.0 is already published" check 0.999.0
  expect pass "$pkg: verify accepts latest on 2.0.0" "" verify
  publish "$pkg" 0.228.0 0.226.0 0.227.0 0.228.0 2.0.0
  expect fail "$pkg: verify refuses latest on 0.x while 2.0.0 is published" \
    "npm latest of $pkg is 0.228.0 but 2.0.0 is published. Run 'npm dist-tag add $pkg@2.0.0 latest'." verify
done

# The remaining cases need only two packages, through the PACKAGES override.
PACKAGES="@daytona/sdk @daytona/api-client"
publish @daytona/sdk 2.0.0 0.226.0 0.227.0 2.0.0
publish @daytona/api-client 0.227.0 0.226.0 0.227.0

# A later 2.x minor keeps blocking.
publish @daytona/sdk 2.1.0 0.227.0 2.0.0 2.1.0
expect fail "0.x cannot take latest above 2.1.0" "@daytona/sdk@2.1.0 is already published" check 0.228.0

# Prereleases never block: while 2.x is prerelease-only on npm the guard alone
# lets 0.x keep latest. sdk_publish.yaml closes that window with
# DAYTONA_V0_CUTOVER, which refuses npm_tag latest on release branches.
publish @daytona/sdk 0.227.0 0.227.0 2.0.0-alpha.4 2.0.0-rc.1
expect pass "2.x prereleases alone do not block a 0.x latest" "" check 0.228.0

# Registry failures fail closed.
rm "${work}/registry/_daytona_sdk.versions"
expect fail "an unreadable registry fails closed" "Could not read the published versions of @daytona/sdk" check 0.228.0

if [ "$failures" -ne 0 ]; then
  echo "$failures failure(s)"
  exit 1
fi
echo "npm-latest-guard: all checks passed"
