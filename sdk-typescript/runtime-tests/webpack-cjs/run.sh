#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
rm -rf node_modules package-lock.json dist
npm install --silent
npm install --silent "$API_CLIENT_TARBALL" "$TOOLBOX_API_CLIENT_TARBALL" "$ANALYTICS_API_CLIENT_TARBALL" "$SDK_TARBALL"
npm run build:cjs
npm run build:esm
node dist/cjs/main.js
node dist/esm/main.js
# The CommonJS bundle is self-contained: it must also run with no node_modules in reach.
isolated="$(mktemp -d)"
trap 'rm -rf "$isolated"' EXIT
cp -r dist/cjs/. "$isolated/"
(cd "$isolated" && env -u NODE_PATH node main.js)
