#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: AGPL-3.0

# Cross-build the daytona CLI for the six release targets
# and write SHA256SUMS. Static (CGO_ENABLED=0), version stamped via ldflags.
#
#   hack/build.sh                 # all six into dist/
#   VERSION=2.0.0-alpha.3 hack/build.sh
#   TARGETS="linux/amd64" hack/build.sh
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${VERSION:-0.0.0-dev}"
DIST="${DIST:-dist}"
TARGETS="${TARGETS:-linux/amd64 linux/arm64 darwin/amd64 darwin/arm64 windows/amd64 windows/arm64}"
LDFLAGS="-s -w -X github.com/daytona/clients/cli/internal/app.Version=${VERSION}"

rm -rf "$DIST" && mkdir -p "$DIST"
for t in $TARGETS; do
  os="${t%/*}"; arch="${t#*/}"
  out="$DIST/daytona-${os}-${arch}"
  [ "$os" = windows ] && out="${out}.exe"
  echo "building $out"
  CGO_ENABLED=0 GOOS="$os" GOARCH="$arch" go build -trimpath -ldflags "$LDFLAGS" -o "$out" .
done
# Every binary must come from a toolchain at or above go.mod's `go` line.
hack/check-binaries.sh "$(sed -n 's/^go //p' go.mod)" "$DIST"/daytona-*
# The install scripts ship as release assets beside the binaries and are
# verified against the same SHA256SUMS by the publish job's resume path and by
# anyone checking a download — so they are checksummed here too.
cp install.sh install.ps1 "$DIST/"
# stock macOS has shasum, not sha256sum
if command -v sha256sum >/dev/null 2>&1; then SHA="sha256sum"; else SHA="shasum -a 256"; fi
( cd "$DIST" && $SHA daytona-* install.sh install.ps1 > SHA256SUMS )
echo "wrote $DIST/SHA256SUMS"
