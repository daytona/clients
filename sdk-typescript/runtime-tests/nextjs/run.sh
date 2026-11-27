#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Runs the API routes three times: from the default `next build` (Turbopack) with
# `next start`, and from two webpack builds with `output: 'standalone'`, without and with the
# outputFileTracingIncludes that the SDK documents for otelEnabled. A standalone server runs
# from a copy outside this project while the project's node_modules is moved away, so it
# only finds the modules the output tracing copied, as after a deployment. /api/create
# uploads a Dockerfile context to stub-api.js; the other routes use the real API.

set -euo pipefail
rm -rf node_modules package-lock.json .next
npm install --silent
npm install --silent "$API_CLIENT_TARBALL" "$TOOLBOX_API_CLIENT_TARBALL" "$ANALYTICS_API_CLIENT_TARBALL" "$SDK_TARBALL"
STANDALONE_DIR="$(cd "$(mktemp -d)" && pwd -P)"
PORT=${RUNTIME_TEST_PORT:-3801}
STUB_PORT=$((PORT + 100))
node stub-api.js "$STUB_PORT" >/tmp/nextjs-stub.log 2>&1 &
STUB_PID=$!
export DAYTONA_STUB_API_URL="http://127.0.0.1:$STUB_PORT"

# Each server runs in its own process group (setsid), so stopping it also stops the
# next-server process that `next start` and server.js leave running under another name.
SERVER_PGID=
stop_server() {
  if [ -n "$SERVER_PGID" ]; then
    kill -9 -- "-$SERVER_PGID" 2>/dev/null || true
    for _ in $(seq 1 50); do
      kill -0 -- "-$SERVER_PGID" 2>/dev/null || break
      sleep 0.1
    done
    SERVER_PGID=
  fi
}

cleanup() {
  local status=$?
  if [ "$status" -ne 0 ]; then
    echo "--- next build output (tail) ---"
    tail -n 60 /tmp/nextjs-build.log 2>/dev/null || true
    echo "--- next server output (tail) ---"
    tail -n 60 /tmp/nextjs-runtime.log 2>/dev/null || true
  fi
  stop_server
  restore_node_modules
  kill -9 "$STUB_PID" 2>/dev/null || true
  rm -rf "$STANDALONE_DIR"
}
trap cleanup EXIT
trap 'exit 143' TERM

hide_node_modules() {
  mv node_modules node_modules.hidden
}
restore_node_modules() {
  if [ -d node_modules.hidden ]; then
    rm -rf node_modules
    mv node_modules.hidden node_modules
  fi
}

port_in_use() {
  (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null
}

# start_server <working dir> <command...>
start_server() {
  local dir="$1"
  shift
  if port_in_use; then
    echo "FAIL: port $PORT is already in use before starting the next server"
    exit 1
  fi
  (cd "$dir" && PORT="$PORT" HOSTNAME=localhost exec setsid "$@") >/tmp/nextjs-runtime.log 2>&1 &
  SERVER_PGID=$!
}

# check_routes <label> <expected server working directory> <otel: ok|missing>
check_routes() {
  local label="$1" expected_cwd="$2" otel="$3"
  for _ in $(seq 1 30); do
    kill -0 "$SERVER_PGID" 2>/dev/null || { echo "FAIL ($label): the next server exited"; exit 1; }
    if curl -sf -m 5 "http://localhost:$PORT/api/sandboxes" >/dev/null 2>&1; then break; fi
    sleep 1
  done

  RESPONSE=$(curl -sf -m 10 "http://localhost:$PORT/api/sandboxes")
  echo "$label response: $RESPONSE"

  # The server that answered must be the one this phase started.
  echo "$RESPONSE" | grep -qF "\"cwd\":\"$expected_cwd\"" || { echo "FAIL ($label): answered by a server in another directory"; exit 1; }
  echo "$RESPONSE" | grep -q '"imageOk":true' || { echo "FAIL ($label): imageOk false"; exit 1; }
  echo "$RESPONSE" | grep -q '"imageFilesOk":true' || { echo "FAIL ($label): imageFilesOk false"; exit 1; }
  echo "$RESPONSE" | grep -q '"listOk":true' || { echo "FAIL ($label): listOk false"; exit 1; }

  RESPONSE=$(curl -sf -m 60 "http://localhost:$PORT/api/create")
  echo "$label create response: $RESPONSE"
  echo "$RESPONSE" | grep -q '"createOk":true' || { echo "FAIL ($label): createOk false"; exit 1; }

  RESPONSE=$(curl -sf -m 30 "http://localhost:$PORT/api/otel")
  echo "$label otel response: $RESPONSE"
  if [ "$otel" = ok ]; then
    echo "$RESPONSE" | grep -q '"otelOk":true' || { echo "FAIL ($label): otelOk false"; exit 1; }
  else
    # Without the include, otelEnabled must fail with the SDK's explanation, not a bare
    # module-not-found error.
    for expected in '"otelOk":false' \
      'OpenTelemetry instrumentation is not supported: The \"@opentelemetry/sdk-node\" package could not be loaded at runtime' \
      "needed even with serverExternalPackages, because output tracing cannot follow this runtime load:\\noutputFileTracingIncludes: {"; do
      echo "$RESPONSE" | grep -qF "$expected" || { echo "FAIL ($label): otel error does not contain: $expected"; exit 1; }
    done
  fi

  stop_server
}

PROJECT_DIR="$(pwd -P)"

ls node_modules/@next >/tmp/nextjs-build.log 2>&1 || true
npm run build >>/tmp/nextjs-build.log 2>&1
if grep -i -E 'warn|critical dependency' /tmp/nextjs-build.log | grep -v -E 'NODE_ENV|React 18'; then
  echo "FAIL (turbopack): next build reported warnings"
  exit 1
fi
start_server "$PROJECT_DIR" node_modules/.bin/next start -p "$PORT"
check_routes "turbopack" "$PROJECT_DIR" ok

# check_standalone <label> <otel: ok|missing> [env for next build...]
check_standalone() {
  local label="$1" otel="$2"
  shift 2
  rm -rf .next "$STANDALONE_DIR"
  mkdir -p "$STANDALONE_DIR"
  env NEXT_STANDALONE=1 "$@" npx next build --webpack >/tmp/nextjs-build.log 2>&1
  if grep -i -E 'warn|critical dependency' /tmp/nextjs-build.log | grep -v -E 'NODE_ENV|React 18'; then
    echo "FAIL ($label): next build reported warnings"
    exit 1
  fi
  cp -r .next/standalone/. "$STANDALONE_DIR/"
  hide_node_modules
  start_server "$STANDALONE_DIR" node server.js
  check_routes "$label" "$STANDALONE_DIR" "$otel"
  restore_node_modules
}

check_standalone "webpack standalone" missing
check_standalone "webpack standalone with otel includes" ok NEXT_OTEL_INCLUDES=1

echo "PASS"
