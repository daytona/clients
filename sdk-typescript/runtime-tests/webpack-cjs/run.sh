#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
rm -rf node_modules package-lock.json dist
npm install --silent
npm install --silent "$API_CLIENT_TARBALL" "$TOOLBOX_API_CLIENT_TARBALL" "$ANALYTICS_API_CLIENT_TARBALL" "$SDK_TARBALL"
npm run build:cjs
npm run build:esm 2>&1 | tee esm-build.log
# The ESM build must bundle without warnings: the SDK keeps OpenTelemetry (and the
# require-in-the-middle it pulls in) out of the bundle and uses only literal imports.
if grep -q 'WARNING' esm-build.log; then
  echo "FAIL: webpack reported warnings for the ESM build"
  exit 1
fi
echo "ESM bundle: $(du -sb dist/esm | cut -f1) bytes"
node dist/cjs/main.js
node dist/esm/main.js
# Both bundles are self-contained: they must also run with no node_modules in reach.
isolated="$(mktemp -d)"
trap 'rm -rf "$isolated"' EXIT
for build in cjs esm; do
  mkdir -p "$isolated/$build"
  cp -r "dist/$build/." "$isolated/$build/"
  (cd "$isolated/$build" && env -u NODE_PATH node main.js)
done
