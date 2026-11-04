#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: AGPL-3.0

# Regenerate the vendored wire types from the public API contracts
# (../contract/{api,toolbox}-openapi.json). Types only — the transport is
# handwritten.
set -euo pipefail
cd "$(dirname "$0")/.."
# Pinned: the generated header names the version, so a different generator
# would make `scripts/gen-contract-types.sh --check` read the committed types as stale.
WANT="v2.8.0"
GOBIN="$(go env GOBIN)"
if [ -n "$GOBIN" ]; then
  DEFAULT_OAPI_CODEGEN="$GOBIN/oapi-codegen"
else
  DEFAULT_OAPI_CODEGEN="$(go env GOPATH)/bin/oapi-codegen"
fi
BIN="${OAPI_CODEGEN:-$DEFAULT_OAPI_CODEGEN}"
if ! [ -x "$BIN" ] || ! "$BIN" -version 2>/dev/null | grep -q "$WANT"; then
  echo "installing oapi-codegen $WANT" >&2
  GOFLAGS=-mod=mod go install "github.com/oapi-codegen/oapi-codegen/v2/cmd/oapi-codegen@$WANT"
  BIN="$DEFAULT_OAPI_CODEGEN"
fi
"$BIN" -generate types -package api -o gen/api/types.gen.go ../contract/api-openapi.json
# Toolbox: the four computer-use screenshot operations declare both
# `show_cursor` and a camelCase `showCursor` query alias, which collide as Go
# fields in the generated *Params types. Computer-use is out of the CLI's
# scope, so those operations are excluded from generation;
# every schema and every other operation is generated.
CONFIG_PATH="$(mktemp "${TMPDIR:-/tmp}/daytona-oapi.XXXXXX")"
trap 'rm -f "$CONFIG_PATH"' EXIT
cat > "$CONFIG_PATH" <<'EOF2'
package: toolbox
output: gen/toolbox/types.gen.go
generate:
  models: true
output-options:
  exclude-operation-ids:
    - screenshot
    - screenshot_compressed
    - screenshot_region
    - screenshot_region_compressed
EOF2
"$BIN" -config "$CONFIG_PATH" ../contract/toolbox-openapi.json
gofmt -w gen
echo "generated gen/api/types.gen.go gen/toolbox/types.gen.go"
