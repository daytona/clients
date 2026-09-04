#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
rm -rf node_modules package-lock.json .next
npm install --silent
npm install --silent "$API_CLIENT_TARBALL" "$TOOLBOX_API_CLIENT_TARBALL" "$ANALYTICS_API_CLIENT_TARBALL" "$SDK_TARBALL"
cleanup() {
  local status=$?
  if [ "$status" -ne 0 ]; then
    echo "--- next build output (tail) ---"
    tail -n 60 /tmp/nextjs-build.log 2>/dev/null || true
    echo "--- next start output (tail) ---"
    tail -n 60 /tmp/nextjs-runtime.log 2>/dev/null || true
  fi
  if [ -n "${PID:-}" ]; then
    kill -9 "$PID" 2>/dev/null || true
  fi
  pkill -9 -f 'next start' 2>/dev/null || true
}
trap cleanup EXIT

npm run build >/tmp/nextjs-build.log 2>&1

PORT=${RUNTIME_TEST_PORT:-3801}
npx next start -p "$PORT" >/tmp/nextjs-runtime.log 2>&1 &
PID=$!

for i in $(seq 1 30); do
  if curl -sf "http://localhost:$PORT/api/sandboxes" >/dev/null 2>&1; then break; fi
  sleep 1
done

RESPONSE=$(curl -sf -m 10 "http://localhost:$PORT/api/sandboxes")
echo "Response: $RESPONSE"

echo "$RESPONSE" | grep -q '"imageOk":true' || { echo "FAIL: imageOk false"; exit 1; }
echo "$RESPONSE" | grep -q '"listOk":true' || { echo "FAIL: listOk false"; exit 1; }
echo "PASS"
