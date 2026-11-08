#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: AGPL-3.0

# Check release binaries with `go version -m`: each one was built by a Go
# toolchain at or above the `go` line of go.mod (a toolchain below it would
# ship standard-library fixes the CLI requires missing), statically
# (CGO_ENABLED=0) and with -trimpath, for the OS/architecture its name says.
#
#   hack/check-binaries.sh MIN_GO_VERSION dist/daytona-linux-amd64 …
#
# hack/build.sh runs it on everything it builds.
set -euo pipefail

[ $# -ge 2 ] || { echo "usage: $(basename "$0") MIN_GO_VERSION BINARY..." >&2; exit 2; }
min="$1"; shift

# at_least A B: version A >= version B, numerically per dot-separated field
# (no `sort -V`: macOS's sort lacks it).
at_least() {
  awk -v a="$1" -v b="$2" 'BEGIN {
    na = split(a, x, "."); nb = split(b, y, "."); n = na > nb ? na : nb
    for (i = 1; i <= n; i++) { if (x[i] + 0 > y[i] + 0) exit 0; if (x[i] + 0 < y[i] + 0) exit 1 }
    exit 0
  }'
}

fail=0
for bin in "$@"; do
  bad=0
  if ! info=$(go version -m "$bin" 2>&1); then
    echo "check-binaries: $bin: not a Go binary: $info" >&2
    fail=1; continue
  fi
  built=$(sed -n '1s/^[^:]*: go\([0-9][0-9.]*\).*$/\1/p' <<<"$info")
  if [ -z "$built" ] || ! at_least "$built" "$min"; then
    echo "check-binaries: $bin was built with go${built:-?}, below the required go${min}" >&2
    bad=1
  fi
  name=$(basename "$bin" .exe)
  goos=$(cut -d- -f2 <<<"$name"); goarch=$(cut -d- -f3 <<<"$name")
  for want in "build	CGO_ENABLED=0" "build	-trimpath=true" "build	GOOS=${goos}" "build	GOARCH=${goarch}"; do
    if ! grep -qxF "	${want}" <<<"$info"; then
      echo "check-binaries: $bin lacks '${want//	/ }' in its build information" >&2
      bad=1
    fi
  done
  if [ "$bad" -eq 0 ]; then echo "$bin: go${built} ${goos}/${goarch}"; else fail=1; fi
done
exit "$fail"
