#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Regenerate the wire models under internal/gen/{api,toolbox} from the contract
# artifacts in ../contract/. Models only (component schemas): the SDK owns the
# transport, and no generated type is part of its public API.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

# Pinned: the generated header names the version, so a different generator
# would make `scripts/gen-contract-types.sh --check` read the models as stale.
# The CLI (cli/hack/gen.sh) pins the same version.
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

# The four toolbox screenshot operations declare both `show_cursor` and a
# camelCase `showCursor` query alias, which collide as Go fields in their
# generated *Params types; the SDK builds those queries itself, so they are
# left out (as in cli/hack/gen.sh). Every schema is still generated.
exclude_toolbox='  exclude-operation-ids: [screenshot, screenshot_compressed, screenshot_region, screenshot_region_compressed]'

# The API spec's `webhooks` payloads are referenced only from the webhooks
# section, which the generator's pruning does not follow while it still emits
# their request-body aliases: pruning is off for the API spec so those payload
# schemas are generated too.
config=$(mktemp "${TMPDIR:-/tmp}/daytona-oapi.XXXXXX")
trap 'rm -f "$config"' EXIT
for spec in api toolbox; do
  mkdir -p "internal/gen/$spec"
  cat >"$config" <<EOF
package: $spec
output: internal/gen/$spec/models.gen.go
generate:
  models: true
output-options:
  skip-prune: $([ "$spec" = api ] && echo true || echo false)
$([ "$spec" = toolbox ] && echo "$exclude_toolbox")
EOF
  "$BIN" -config "$config" "../contract/$spec-openapi.json"
done
gofmt -w internal/gen
echo "generated internal/gen/api/models.gen.go internal/gen/toolbox/models.gen.go"
