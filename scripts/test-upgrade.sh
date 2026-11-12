#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Gates for the daytona-upgrade migration tool (upgrade/): mapping and guide
# drift, the surface check against both 2.x SDKs, and both packages' suites.
# Needs node, python3 and uv.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

node upgrade/scripts/sync-mapping.mjs --check
node upgrade/scripts/gen-guide.mjs --check
if [ ! -f sdk-typescript/node_modules/typescript/package.json ]; then
  (cd sdk-typescript && npm ci --no-audit --no-fund >/dev/null)
fi
node --test upgrade/scripts/*.test.mjs
node upgrade/scripts/check-v2-surface.mjs
(cd upgrade/typescript && npm ci --no-audit --no-fund && npm run typecheck && npm test)
(cd upgrade/python && uv sync --locked && uv run ruff check . && uv run ruff format --check . && uv run basedpyright && uv run pytest -q -n auto)
