#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Runtime compatibility tests for the @daytona/sdk package.
#
# Packs the built SDK, stamps a version unique to this run into the tarball,
# starts mock-api.mjs (the API and one sandbox toolbox) and, for every
# subdirectory with a run.sh, runs that runtime in a throwaway copy that
# installs the tarball. See README.md.
#
# Needs `npm run build` first (after scripts/bundle-v0.sh for the published,
# v0-bundled shape), or a packed tarball in RUNTIME_TEST_TARBALL.
#   ONLY=a,b                         run a subset
#   RUNTIME_TEST_TARBALL=path.tgz    test this `npm pack` output instead of packing dist/
#   RUNTIME_TEST_PARALLEL=N          concurrency (default: nproc; 1 = sequential)
#   RUNTIME_TEST_TIMEOUT_SECONDS=N   per-runtime wall clock (default 600)
#   RUNTIME_TEST_REQUIRE_V0_BUNDLE=1 fail unless the build carries the v0 bundle
#   CI=true                          a runtime that skips (missing toolchain) fails
#
#   run-all.sh --list                print the runtime names as a JSON array (the CI matrix)

set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PKG="$(cd "$ROOT/.." && pwd)"

if [ "${1:-}" = --list ]; then
  for dir in "$ROOT"/*/; do
    [ -f "$dir/run.sh" ] && basename "$dir"
  done | node -e "process.stdout.write(JSON.stringify(require('fs').readFileSync(0, 'utf8').split('\\n').filter(Boolean)))"
  echo
  exit 0
fi

if [ -z "${RUNTIME_TEST_TARBALL:-}" ] && { [ ! -f "$PKG/dist/esm/index.js" ] || [ ! -f "$PKG/dist/cjs/index.js" ]; }; then
  echo "ERROR: the SDK is not built. Run: (cd sdk-typescript && npm run build)" >&2
  exit 1
fi

# Each runtime does a cold npm install into a throwaway directory; skip the
# registry side calls that add nothing there.
export npm_config_audit=false npm_config_fund=false npm_config_update_notifier=false

TIMEOUT_SECONDS="${RUNTIME_TEST_TIMEOUT_SECONDS:-600}"
PARALLEL="${RUNTIME_TEST_PARALLEL:-$(nproc 2>/dev/null || echo 4)}"
STATE_DIR="$(mktemp -d)"
MOCK_PID=""
# On an early exit (Ctrl-C, a CI cancel), stop every runtime still running.
# Each one runs in its own process group (run_one), and its run.sh stops the
# servers it started when it gets SIGTERM (lib.sh install_traps).
cleanup() {
  local pidfile pid stopped=""
  for pidfile in "$STATE_DIR"/*.pid; do
    [ -f "$pidfile" ] || continue
    pid="$(cat "$pidfile")"
    kill -TERM -- "-$pid" 2>/dev/null && stopped=1
  done
  if [ -n "$stopped" ]; then
    sleep 3
    for pidfile in "$STATE_DIR"/*.pid; do
      [ -f "$pidfile" ] && kill -KILL -- "-$(cat "$pidfile")" 2>/dev/null
    done
  fi
  [ -n "$MOCK_PID" ] && kill "$MOCK_PID" 2>/dev/null
  rm -rf "$STATE_DIR"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

export RUNTIME_TESTS_DIR="$ROOT"
export RUNTIME_LIB="$ROOT/lib.sh"

if [ -n "${RUNTIME_TEST_TARBALL:-}" ]; then
  PACKED_TGZ="$(cd "$(dirname "$RUNTIME_TEST_TARBALL")" && pwd)/$(basename "$RUNTIME_TEST_TARBALL")"
  echo "==> Using $PACKED_TGZ"
else
  echo "==> Packing @daytona/sdk"
  PACKED="$(cd "$PKG" && npm pack --silent --ignore-scripts --pack-destination "$STATE_DIR" | tail -n 1)"
  PACKED_TGZ="$STATE_DIR/$PACKED"
fi
if [ ! -f "$PACKED_TGZ" ]; then
  echo "ERROR: no tarball at $PACKED_TGZ" >&2
  exit 1
fi
mkdir "$STATE_DIR/stage"
tar -xzf "$PACKED_TGZ" -C "$STATE_DIR/stage"
STAGED="$STATE_DIR/stage/package"
if [ ! -f "$STAGED/dist/cjs/v0/index.js" ]; then
  echo "ERROR: the tarball has no dist/cjs/v0 entry" >&2
  exit 1
fi
if grep -q '__daytonaV0Unbundled' "$STAGED/dist/cjs/v0/index.js"; then
  EXPECT_V0=unbundled
else
  EXPECT_V0=bundled
fi
if [ "${RUNTIME_TEST_REQUIRE_V0_BUNDLE:-}" = 1 ] && [ "$EXPECT_V0" != bundled ]; then
  echo "ERROR: RUNTIME_TEST_REQUIRE_V0_BUNDLE=1 but the package has no v0 bundle (run scripts/bundle-v0.sh, npm install, npm run build)" >&2
  exit 1
fi
export RUNTIME_TEST_EXPECT_V0="$EXPECT_V0"
# Provenance: the tarball reports a version no registry has, and the mock
# rejects every request that does not carry it, so a runtime that resolved
# any other @daytona/sdk (a registry release, a cached or stale tarball)
# cannot pass.
BASE_VERSION="$(node -p "require('$STAGED/package.json').version")"
SDK_VERSION="${BASE_VERSION%%-*}-runtime-test.$(date +%s).$$"
node "$ROOT/stamp-tarball.mjs" "$STAGED" "$BASE_VERSION" "$SDK_VERSION" || exit 1
export SDK_TARBALL="$STATE_DIR/daytona-sdk-$SDK_VERSION.tgz"
tar -czf "$SDK_TARBALL" -C "$STATE_DIR/stage" package
echo "    $SDK_TARBALL (v0 namespace: $EXPECT_V0)"

echo "==> Starting the mock API"
MOCK_LOG="$STATE_DIR/mock-api.log"
export DAYTONA_API_KEY="runtime-test-key"
MOCK_API_KEY="$DAYTONA_API_KEY" MOCK_SDK_VERSION="$SDK_VERSION" node "$ROOT/mock-api.mjs" >"$MOCK_LOG" 2>&1 &
MOCK_PID=$!
# Out of the job table, so the `wait`s below see only runtimes.
disown "$MOCK_PID"
MOCK_PORT=""
for _ in $(seq 1 50); do
  MOCK_PORT="$(sed -n 's/^LISTENING \([0-9]*\)$/\1/p' "$MOCK_LOG")"
  [ -n "$MOCK_PORT" ] && break
  sleep 0.1
done
if [ -z "$MOCK_PORT" ]; then
  echo "ERROR: the mock API did not start:" >&2
  cat "$MOCK_LOG" >&2
  exit 1
fi
export DAYTONA_V2_API_URL="http://127.0.0.1:$MOCK_PORT"
# Only the mock: never a v0 endpoint or 0.x setting from the caller's environment.
unset DAYTONA_API_URL DAYTONA_V0_API_URL DAYTONA_JWT_TOKEN DAYTONA_PROJECT_ID DAYTONA_OTEL_ENABLED
echo "    $DAYTONA_V2_API_URL (expects X-Daytona-SDK-Version: $SDK_VERSION)"

declare -a RUNTIMES=()
if [ -n "${ONLY:-}" ]; then
  IFS=',' read -ra RUNTIMES <<<"$ONLY"
else
  for dir in "$ROOT"/*/; do
    [ -f "$dir/run.sh" ] && RUNTIMES+=("$(basename "$dir")")
  done
fi
echo "==> Running ${#RUNTIMES[@]} runtime(s), up to $PARALLEL at a time"
echo

# One runtime in a throwaway copy; its output and `<exit code> <seconds>` go
# to $STATE_DIR so the summary below reports in a stable order.
run_one() {
  local runtime="$1" dir="$ROOT/$1" work start status=0
  local out="$STATE_DIR/$runtime.out" status_file="$STATE_DIR/$runtime.status"
  if [ ! -f "$dir/run.sh" ]; then
    echo "invalid 0" >"$status_file"
    return
  fi
  work="$(mktemp -d)"
  cp -r "$dir"/. "$work/"
  cp "$ROOT/runtime-checks.mjs" "$ROOT/runtime-checks.d.mts" "$work/"
  start=$SECONDS
  # Its own process group (setsid), so cleanup can stop the whole runtime. No
  # ambient NODE_ENV: each framework's production build runs as it would for
  # a user.
  (cd "$work" && exec setsid timeout -k 15 "$TIMEOUT_SECONDS" env -u NODE_ENV bash run.sh) >"$out" 2>&1 &
  local pid=$!
  echo "$pid" >"$STATE_DIR/$runtime.pid"
  wait "$pid" || status=$?
  rm -f "$STATE_DIR/$runtime.pid"
  echo "$status $((SECONDS - start))" >"$status_file"
  rm -rf "$work"
}

running=0
for runtime in "${RUNTIMES[@]}"; do
  run_one "$runtime" &
  running=$((running + 1))
  if [ "$running" -ge "$PARALLEL" ]; then
    wait -n
    running=$((running - 1))
  fi
done
wait

declare -a PASS=() FAIL=() SKIP=() INVALID=()
for runtime in "${RUNTIMES[@]}"; do
  read -r status duration <"$STATE_DIR/$runtime.status"
  if [ "$status" = invalid ]; then
    INVALID+=("$runtime (no run.sh)")
    continue
  fi
  echo "================================================================"
  echo "▶ $runtime"
  echo "================================================================"
  cat "$STATE_DIR/$runtime.out"
  if [ "$status" -eq 0 ] && grep -q '^SKIP:' "$STATE_DIR/$runtime.out"; then
    SKIP+=("$runtime ($(sed -n 's/^SKIP: //p' "$STATE_DIR/$runtime.out" | head -n 1))")
    echo "⊘ $runtime SKIP"
  elif [ "$status" -eq 0 ]; then
    PASS+=("$runtime (${duration}s)")
    echo "✓ $runtime PASS (${duration}s)"
  elif [ "$status" -eq 124 ] || [ "$status" -eq 137 ]; then
    FAIL+=("$runtime (timed out after ${TIMEOUT_SECONDS}s)")
    echo "✗ $runtime TIMEOUT"
  else
    FAIL+=("$runtime (exit $status, ${duration}s)")
    echo "✗ $runtime FAIL (${duration}s)"
  fi
  echo
done

echo "================================================================"
echo "Summary"
echo "================================================================"
report() {
  local label="$1"
  shift
  [ $# -gt 0 ] || return 0
  printf '%s (%d):\n' "$label" "$#"
  printf '  %s\n' "$@"
}
report "✓ PASS" "${PASS[@]+"${PASS[@]}"}"
report "✗ FAIL" "${FAIL[@]+"${FAIL[@]}"}"
report "⊘ SKIP" "${SKIP[@]+"${SKIP[@]}"}"
report "? UNKNOWN" "${INVALID[@]+"${INVALID[@]}"}"

# In CI a skipped runtime means a toolchain install step broke.
if [ -n "${CI:-}" ] && [ "${CI}" != false ] && [ ${#SKIP[@]} -gt 0 ]; then
  echo "ERROR: CI must run every runtime; skipped: ${SKIP[*]}" >&2
  exit 1
fi
[ ${#FAIL[@]} -eq 0 ] && [ ${#INVALID[@]} -eq 0 ] && [ ${#RUNTIMES[@]} -gt 0 ]
