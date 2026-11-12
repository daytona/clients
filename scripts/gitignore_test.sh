#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Checks that build output, dependencies and local files stay ignored wherever
# a tool writes them, and that no tracked file is ignored.
# Run: bash scripts/gitignore_test.sh
set -euo pipefail

cd "$(dirname "$0")/.."

fail() { echo "FAIL: $*" >&2; exit 1; }

ignored=(
  cli/dist/daytona-linux-amd64
  sdk-typescript/dist/index.js
  sdk-python/dist/daytona.whl
  sdk-python/build/lib/x.py
  upgrade/scripts/dist/index.js
  upgrade/typescript/node_modules/x/index.js
  upgrade/scripts/node_modules/x/index.js
  examples/node_modules/x/index.js
  node_modules/x/index.js
  upgrade/python/build/lib/x.py
  examples/python/.venv/bin/python
  sdk-python/src/daytona/__pycache__/x.pyc
  .env.local
  sdk-python/.env.local
  .DS_Store
  cli/.DS_Store
  .idea/workspace.xml
  .vscode/settings.json
)
for path in "${ignored[@]}"; do
  git check-ignore -q --no-index "$path" || fail "$path is not ignored"
done

tracked_ignored=$(git ls-files -ci --exclude-standard)
[ -z "$tracked_ignored" ] || fail "tracked files match an ignore rule: $tracked_ignored"

echo "gitignore tests passed"
