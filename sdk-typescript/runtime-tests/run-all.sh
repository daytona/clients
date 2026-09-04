#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Runtime compatibility tests for @daytona/sdk
#
# For each subdirectory containing a `run.sh`, this orchestrator:
#   1. Installs the locally-built SDK as `@daytona/sdk` via npm pack tarball
#   2. Executes the runtime's `run.sh`
#   3. Records pass/fail and continues to the next runtime
#
# Required env: DAYTONA_API_KEY, DAYTONA_API_URL — passed through to each test.
# Optional env: ONLY="comma,separated,runtimes" to run a subset.

set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
DIST="$(cd "$ROOT/../../dist/sdk-typescript" 2>/dev/null && pwd || true)"

if [ -z "$DIST" ] || [ ! -f "$DIST/package.json" ]; then
  echo "ERROR: SDK not built. Run: yarn nx build sdk-typescript" >&2
  exit 1
fi

if [ -z "${DAYTONA_API_KEY:-}" ] || [ -z "${DAYTONA_API_URL:-}" ]; then
  echo "ERROR: DAYTONA_API_KEY and DAYTONA_API_URL must be set" >&2
  exit 1
fi

# Each runtime does a cold `npm install` into a throwaway directory. Skip the
# registry side-calls that add nothing there (audit, funding, npm self-update
# check): they hit separate registry endpoints whose degradation has stalled
# installs for many minutes and pushed the whole job past its timeout.
export npm_config_audit=false
export npm_config_fund=false
export npm_config_update_notifier=false
# `next build` fetches full registry packuments (no timeout) to patch the
# throwaway lockfile's missing platform-specific @next/swc-* entries, and posts
# telemetry. Neither matters here; both are network calls we don't want to wait on.
export NEXT_IGNORE_INCORRECT_LOCKFILE=1
export NEXT_TELEMETRY_DISABLED=1

# A single stalled runtime must fail visibly (with its output) rather than eat
# the whole job's time budget and surface as "cancelled" with no diagnostics.
RUNTIME_TIMEOUT_SECONDS="${RUNTIME_TEST_TIMEOUT_SECONDS:-480}"

DIST_LIBS="$(cd "$DIST/../" && pwd)"
API_CLIENT_DIST="$DIST_LIBS/api-client"
TOOLBOX_DIST="$DIST_LIBS/toolbox-api-client"
ANALYTICS_DIST="$DIST_LIBS/analytics-api-client"

if [ ! -f "$API_CLIENT_DIST/package.json" ] || [ ! -f "$TOOLBOX_DIST/package.json" ] || [ ! -f "$ANALYTICS_DIST/package.json" ]; then
  echo "ERROR: api-client, toolbox-api-client, or analytics-api-client not built. Run: yarn nx build sdk-typescript" >&2
  exit 1
fi

pack_pkg() {
  local src="$1" dest="$2" expected_name="$3"
  local pack_dir
  pack_dir="$(mktemp -d)"
  cp -r "$src"/. "$pack_dir/"
  node -e "
    const fs = require('fs');
    const p = require('$pack_dir/package.json');
    if (p.name !== '$expected_name') { p.name = '$expected_name'; }
    fs.writeFileSync('$pack_dir/package.json', JSON.stringify(p, null, 2));
  "
  (cd "$pack_dir" && npm pack --silent | head -1 | xargs -I{} mv {} "$dest")
  rm -rf "$pack_dir"
}

echo "==> Packing api-client, toolbox-api-client, analytics-api-client, and sdk"
API_TARBALL="$ROOT/.api-client.tgz"
TOOLBOX_TARBALL="$ROOT/.toolbox-api-client.tgz"
ANALYTICS_TARBALL="$ROOT/.analytics-api-client.tgz"
TARBALL="$ROOT/.sdk.tgz"
pack_pkg "$API_CLIENT_DIST" "$API_TARBALL" "@daytona/api-client"
pack_pkg "$TOOLBOX_DIST" "$TOOLBOX_TARBALL" "@daytona/toolbox-api-client"
pack_pkg "$ANALYTICS_DIST" "$ANALYTICS_TARBALL" "@daytona/analytics-api-client"
pack_pkg "$DIST" "$TARBALL" "@daytona/sdk"
export SDK_TARBALL="$TARBALL"
export API_CLIENT_TARBALL="$API_TARBALL"
export TOOLBOX_API_CLIENT_TARBALL="$TOOLBOX_TARBALL"
export ANALYTICS_API_CLIENT_TARBALL="$ANALYTICS_TARBALL"
echo "    Tarball: $TARBALL"
echo

declare -a RUNTIMES=()
if [ -n "${ONLY:-}" ]; then
  IFS=',' read -ra RUNTIMES <<< "$ONLY"
else
  for dir in "$ROOT"/*/; do
    name="$(basename "$dir")"
    [ -f "$dir/run.sh" ] && RUNTIMES+=("$name")
  done
fi

declare -a PASS=()
declare -a FAIL=()
declare -a SKIP=()
declare -a INVALID=()

for runtime in "${RUNTIMES[@]}"; do
  dir="$ROOT/$runtime"
  if [ ! -f "$dir/run.sh" ]; then
    INVALID+=("$runtime (no run.sh)")
    continue
  fi

  echo "================================================================"
  echo "▶ $runtime"
  echo "================================================================"

  WORK_DIR="$(mktemp -d)"
  cp -r "$dir"/. "$WORK_DIR/"

  start=$SECONDS
  # Strip any ambient NODE_ENV (e.g. the Nix dev shell sets development) so each
  # framework's production build/prerender runs with the React production build.
  status=0
  (cd "$WORK_DIR" && timeout -k 15 "$RUNTIME_TIMEOUT_SECONDS" env -u NODE_ENV bash run.sh) || status=$?
  if [ "$status" -eq 0 ]; then
    duration=$((SECONDS - start))
    PASS+=("$runtime (${duration}s)")
    echo "✓ $runtime PASS (${duration}s)"
  elif [ "$status" -eq 124 ] || [ "$status" -eq 137 ]; then
    FAIL+=("$runtime (timed out after ${RUNTIME_TIMEOUT_SECONDS}s)")
    echo "✗ $runtime TIMEOUT (${RUNTIME_TIMEOUT_SECONDS}s)"
    for log in /tmp/"$runtime"-*.log; do
      [ -f "$log" ] || continue
      echo "--- $(basename "$log") (tail) ---"
      tail -n 60 "$log"
    done
  else
    duration=$((SECONDS - start))
    FAIL+=("$runtime (${duration}s)")
    echo "✗ $runtime FAIL (${duration}s)"
  fi
  rm -rf "$WORK_DIR"
  echo
done

rm -f "$TARBALL" "$API_TARBALL" "$TOOLBOX_TARBALL" "$ANALYTICS_TARBALL"

echo "================================================================"
echo "Summary"
echo "================================================================"
[ ${#PASS[@]} -gt 0 ] && printf '✓ PASS (%d):\n  %s\n' "${#PASS[@]}" "$(IFS=$'\n  '; echo "${PASS[*]}")"
[ ${#FAIL[@]} -gt 0 ] && printf '✗ FAIL (%d):\n  %s\n' "${#FAIL[@]}" "$(IFS=$'\n  '; echo "${FAIL[*]}")"
[ ${#SKIP[@]} -gt 0 ] && printf '⊘ SKIP (%d):\n  %s\n' "${#SKIP[@]}" "$(IFS=$'\n  '; echo "${SKIP[*]}")"
[ ${#INVALID[@]} -gt 0 ] && printf '? UNKNOWN (%d):\n  %s\n' "${#INVALID[@]}" "$(IFS=$'\n  '; echo "${INVALID[*]}")"

[ ${#FAIL[@]} -eq 0 ] && [ ${#INVALID[@]} -eq 0 ]
