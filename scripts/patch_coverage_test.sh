#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Exercise the patch-coverage CLI against an isolated Go SDK diff.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
git init -q
mkdir -p sdk-go/internal/gen/api
printf 'module go.daytona.com/sdk/v2\n\ngo 1.26.0\n' > sdk-go/go.mod
printf 'package daytona\n\nfunc covered() int { return 1 }\n' > sdk-go/covered.go
printf 'package api\n\nconst Generated = 1\n' > sdk-go/internal/gen/api/api.go
printf 'package daytona\n\nconst Generated = 1\n' > sdk-go/codes_catalog_0.go
git add .
git -c user.name=Test -c user.email=test@example.com commit -qm fixture
printf 'package daytona\n\nfunc covered() int { return 2 }\n' > sdk-go/covered.go
printf 'package api\n\nconst Generated = 2\n' > sdk-go/internal/gen/api/api.go
printf 'package daytona\n\nconst Generated = 2\n' > sdk-go/codes_catalog_0.go

run_case() {
  local expected=$1 status=0
  python3 "$root/scripts/patch_coverage.py" --base HEAD > output 2>&1 || status=$?
  [ "$status" -eq "$expected" ] || { cat output; echo "FAIL: exit $status, expected $expected" >&2; exit 1; }
}

# Given changed SDK source, a missing report must fail rather than silently skip it.
# When measuring without a profile.
run_case 1
# Then the SDK is a measured component and generated files are excluded.
grep -qF 'MISSING REPORT: sdk-go/coverage.out' output
grep -qF 'ignored (generated/test/example): 2 file(s)' output
echo "PASS: Go SDK report required; generated files ignored"

# Given a profile whose module path is the Go SDK's vanity path.
printf 'mode: set\ngo.daytona.com/sdk/v2/covered.go:3.1,3.33 1 1\n' > sdk-go/coverage.out
# When measuring covered added lines.
run_case 0
# Then those lines count toward patch coverage.
grep -qF 'patch coverage: 1/1 added lines covered = 100.00%' output
echo "PASS: Go SDK covered lines measured"

# Given the same change with no test hits.
printf 'mode: set\ngo.daytona.com/sdk/v2/covered.go:3.1,3.33 1 0\n' > sdk-go/coverage.out
# When measuring uncovered added lines.
run_case 1
# Then the patch target fails.
grep -qF 'patch coverage: 0/1 added lines covered = 0.00%' output
echo "PASS: Go SDK uncovered lines fail the target"
