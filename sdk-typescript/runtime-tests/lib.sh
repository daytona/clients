# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Shared helpers for the per-runtime run.sh scripts. Sourced, not executed:
#   source "$RUNTIME_LIB"
# run-all.sh runs runtimes concurrently, so every helper stays parallel-safe:
# no fixed ports, no pkill by name, no shared temp paths.

set -euo pipefail

: "${RUNTIME_TESTS_DIR:?run via run-all.sh}"
: "${SDK_TARBALL:?run via run-all.sh}"

# Print `SKIP: <reason>` and stop when a binary the runtime needs is missing.
# run-all.sh counts it as skipped locally and as a failure under CI=true.
require_binary() {
  local binary="$1" hint="$2"
  if ! command -v "$binary" >/dev/null 2>&1; then
    echo "SKIP: $binary not found ($hint)"
    exit 0
  fi
}

# Fresh install of the runtime's own dependencies plus the packed SDK.
install_sdk() {
  rm -rf node_modules package-lock.json
  if node -e "const p=require('./package.json');process.exit(p.dependencies||p.devDependencies?0:1)"; then
    npm install --silent
  fi
  npm install --silent "$SDK_TARBALL"
}

# Check a runtime's JSON result (see assert-result.mjs). Capture the output in
# an assignment first (`result="$(cmd)"`), so set -e fails the runtime when
# the command itself fails.
#   assert_response <json>
assert_response() {
  printf '%s' "$1" | node "$RUNTIME_TESTS_DIR/assert-result.mjs"
}

# Ask the kernel for a free TCP port. The listener closes before the caller
# binds, so a concurrent runtime could take the port in between;
# start_server_on_free_port retries on that race.
free_port() {
  node -e "const s=require('net').createServer();s.listen(0,'127.0.0.1',()=>{process.stdout.write(String(s.address().port));s.close()})"
}

# Start a long-running server in its own process group, so teardown stops the
# whole tree (npx -> next -> workers) without touching sibling runtimes.
#   start_server <logfile> <cmd...>   -> sets SERVER_PID, SERVER_LOG
start_server() {
  SERVER_LOG="$1"
  shift
  setsid "$@" >"$SERVER_LOG" 2>&1 &
  SERVER_PID=$!
}

stop_server() {
  [ -n "${SERVER_PID:-}" ] || return 0
  kill -TERM -- "-$SERVER_PID" 2>/dev/null || kill -TERM "$SERVER_PID" 2>/dev/null || true
  sleep 1
  kill -KILL -- "-$SERVER_PID" 2>/dev/null || kill -KILL "$SERVER_PID" 2>/dev/null || true
  SERVER_PID=""
}

# Poll until the URL answers 2xx or the wall-clock budget (seconds) runs out.
#   wait_for_url <url> [seconds=60]
wait_for_url() {
  local url="$1" budget="${2:-60}" deadline
  deadline=$((SECONDS + budget))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if curl -sf -m 5 -o /dev/null "$url"; then return 0; fi
    if [ -n "${SERVER_PID:-}" ] && ! kill -0 "$SERVER_PID" 2>/dev/null; then
      echo "FAIL: server exited before answering $url"
      return 1
    fi
    sleep 1
  done
  echo "FAIL: $url did not answer within ${budget}s"
  return 1
}

# Start a server whose port must be chosen up front (framework CLIs take
# `--port` / `PORT`). Substitutes a free port for the literal `{PORT}` in the
# URL template and the arguments, waits for the URL, and retries with a new
# port when the server lost the bind race.
#   start_server_on_free_port <logfile> <url-template> <cmd...>   -> sets PORT, SERVER_PID, SERVER_LOG
start_server_on_free_port() {
  local log="$1" url_template="$2" attempt arg
  shift 2
  local -a args
  for attempt in 1 2 3; do
    PORT="$(free_port)"
    args=()
    for arg in "$@"; do args+=("${arg//\{PORT\}/$PORT}"); done
    start_server "$log" "${args[@]}"
    if wait_for_url "${url_template//\{PORT\}/$PORT}" "${WAIT_FOR_URL_SECONDS:-60}"; then return 0; fi
    if grep -qiE 'EADDRINUSE|address already in use' "$log" 2>/dev/null; then
      echo "port $PORT was taken before the server bound it (attempt $attempt); retrying"
      stop_server
      continue
    fi
    return 1
  done
  echo "FAIL: no free port after 3 attempts"
  return 1
}

# On failure print the tail of the build and server logs, then stop the server.
on_exit() {
  local status=$? log
  if [ "$status" -ne 0 ]; then
    for log in "${BUILD_LOG:-}" "${SERVER_LOG:-}"; do
      [ -n "$log" ] && [ -f "$log" ] || continue
      echo "--- $(basename "$log") (tail) ---"
      tail -n 60 "$log"
    done
  fi
  stop_server
}

install_traps() {
  trap on_exit EXIT
  trap 'exit 143' TERM
}
