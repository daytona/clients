#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Runs the API route twice: from the default `next build` (Turbopack) with `next start`, and
# from a webpack build with `output: 'standalone'`, copied outside this project so the
# server only finds the modules the output tracing copied.

set -euo pipefail
rm -rf node_modules package-lock.json .next
npm install --silent
npm install --silent "$API_CLIENT_TARBALL" "$TOOLBOX_API_CLIENT_TARBALL" "$ANALYTICS_API_CLIENT_TARBALL" "$SDK_TARBALL"
STANDALONE_DIR="$(mktemp -d)"
cleanup() {
  local status=$?
  if [ "$status" -ne 0 ]; then
    echo "--- next build output (tail) ---"
    tail -n 60 /tmp/nextjs-build.log 2>/dev/null || true
    echo "--- next server output (tail) ---"
    tail -n 60 /tmp/nextjs-runtime.log 2>/dev/null || true
  fi
  if [ -n "${PID:-}" ]; then
    kill -9 "$PID" 2>/dev/null || true
  fi
  pkill -9 -f 'next start' 2>/dev/null || true
  rm -rf "$STANDALONE_DIR"
}
trap cleanup EXIT
trap 'exit 143' TERM

PORT=${RUNTIME_TEST_PORT:-3801}

check_route() {
  local label="$1"
  for i in $(seq 1 30); do
    if curl -sf -m 5 "http://localhost:$PORT/api/sandboxes" >/dev/null 2>&1; then break; fi
    sleep 1
  done

  RESPONSE=$(curl -sf -m 10 "http://localhost:$PORT/api/sandboxes")
  echo "$label response: $RESPONSE"

  echo "$RESPONSE" | grep -q '"imageOk":true' || { echo "FAIL ($label): imageOk false"; exit 1; }
  echo "$RESPONSE" | grep -q '"imageFilesOk":true' || { echo "FAIL ($label): imageFilesOk false"; exit 1; }
  echo "$RESPONSE" | grep -q '"listOk":true' || { echo "FAIL ($label): listOk false"; exit 1; }

  kill -9 "$PID" 2>/dev/null || true
  wait "$PID" 2>/dev/null || true
  PID=
}

ls node_modules/@next >/tmp/nextjs-build.log 2>&1 || true
npm run build >>/tmp/nextjs-build.log 2>&1
npx next start -p "$PORT" >/tmp/nextjs-runtime.log 2>&1 &
PID=$!
check_route "turbopack"

rm -rf .next
NEXT_STANDALONE=1 npx next build --webpack >/tmp/nextjs-build.log 2>&1
cp -r .next/standalone/. "$STANDALONE_DIR/"
(cd "$STANDALONE_DIR" && PORT="$PORT" HOSTNAME=localhost exec node server.js) >/tmp/nextjs-runtime.log 2>&1 &
PID=$!
check_route "webpack standalone"

echo "PASS"
