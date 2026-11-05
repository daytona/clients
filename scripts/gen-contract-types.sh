#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Regenerate every vendored wire type from the committed contract/ artifacts:
# the TypeScript SDK (sdk-typescript/src/v2/gen), the Python SDK
# (sdk-python/src/daytona/v2/_gen) and the CLI (cli/gen).
#
#   scripts/gen-contract-types.sh           # regenerate
#   scripts/gen-contract-types.sh --check   # regenerate, then fail if anything changed
#
# Needs node, a JRE (the Python generator) and Go on PATH.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

check=false
case "${1:-}" in
  --check) check=true ;;
  "") ;;
  *) echo "usage: $0 [--check]" >&2; exit 2 ;;
esac

(cd sdk-typescript && npm install --no-audit --no-fund --silent && npm run --silent gen:contract)
(cd sdk-python && ./gen-contract.sh)
(cd cli && ./hack/gen.sh)

if $check; then
  paths="sdk-typescript/src/v2/gen sdk-python/src/daytona/v2/_gen cli/gen"
  stale=0
  # shellcheck disable=SC2086 # word splitting is the point
  git diff --exit-code -- $paths || stale=1
  # shellcheck disable=SC2086
  untracked=$(git ls-files --others --exclude-standard -- $paths)
  if [ -n "$untracked" ]; then echo "untracked generated files:"; echo "$untracked"; stale=1; fi
  if [ "$stale" -ne 0 ]; then
    echo "vendored wire types are stale — run scripts/gen-contract-types.sh and commit" >&2
    exit 1
  fi
  echo "vendored wire types match contract/"
fi
