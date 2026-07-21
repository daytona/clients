#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
rm -rf node_modules package-lock.json .nuxt .output
# package.json pins @vitejs/devtools to 0.3.1 via overrides: 0.4.2 (2026-07-21)
# introduced a circular peer set (devtools-oxc/-rolldown/-vite) that crashes
# npm's arborist ("Cannot read properties of null (reading 'edgesOut')").
# Drop the override once npm or @vitejs/devtools ships a fix.
npm install --silent
npm install --silent "$API_CLIENT_TARBALL" "$TOOLBOX_API_CLIENT_TARBALL" "$ANALYTICS_API_CLIENT_TARBALL" "$SDK_TARBALL"
npm run build >/dev/null

PORT=${RUNTIME_TEST_PORT:-3802}
PORT="$PORT" node .output/server/index.mjs >/tmp/nuxt-runtime.log 2>&1 &
PID=$!
trap "kill -9 $PID 2>/dev/null || true" EXIT

for i in $(seq 1 30); do
  if curl -sf "http://localhost:$PORT/api/sandboxes" >/dev/null 2>&1; then break; fi
  sleep 1
done

RESPONSE=$(curl -sf -m 10 "http://localhost:$PORT/api/sandboxes")
echo "Response: $RESPONSE"

echo "$RESPONSE" | grep -q '"imageOk":true' || { echo "FAIL: imageOk false"; exit 1; }
echo "$RESPONSE" | grep -q '"listOk":true' || { echo "FAIL: listOk false"; exit 1; }
echo "PASS"
