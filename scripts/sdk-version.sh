#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Derive the SDK and CLI publish versions from a release version.
#
# sdk_publish.yaml publishes one `vX.Y.Z[-pre.N]` version to every target and
# this script is the one place that turns it into the strings each registry
# wants, so the workflow, the docs and the table test agree.
#
#   sdk-version.sh v2.0.0-alpha.3      # or 2.0.0-alpha.3
#     version=2.0.0-alpha.3            # the release version (input minus `v`)
#     npm=2.0.0-alpha.3                # @daytona/sdk — SemVer, unchanged
#     cli=2.0.0-alpha.3                # daytona CLI — SemVer, unchanged
#     cli_tag=v2.0.0-alpha.3           # the release tag in this repository
#     pypi=2.0.0a3                     # daytona on PyPI — PEP 440 spelling
#
#   sdk-version.sh --pep440 2.0.0-rc.1 # prints only the PEP 440 spelling: 2.0.0rc1
#   sdk-version.sh --go v2.1.0 sdk-go/go.mod
#     go=v2.1.0
#     go_tag=sdk-go/v2.1.0
#     go_module=go.daytona.com/sdk/v2
# --go reads (never executes) the source module declaration and refuses majors
# below 2 or a mismatched path: sdk-go/v0.* and v1.* would poison the 0.x module.
#
# Accepted input: `v`-prefixed or bare `MAJOR.MINOR.PATCH`, optionally followed
# by `-alpha.N`, `-beta.N` or `-rc.N`; numeric parts without leading zeros (npm
# and PEP 440 would canonicalize `2.01.0` to `2.1.0`, and the summary and the
# registry links would then disagree with the tag). Anything else — build
# metadata, a bare `-alpha`, a non-version prefix, an empty string — exits 1 with a
# message on stderr and prints nothing, so a caller that appends the output
# to $GITHUB_OUTPUT never half-populates it.
#
# PEP 440 mapping (https://peps.python.org/pep-0440/#pre-releases):
#   -alpha.N → aN   -beta.N → bN   -rc.N → rcN   stable → unchanged
#
# Test: bash scripts/sdk-version_test.sh
set -euo pipefail

usage() {
  echo "usage: $(basename "$0") [--pep440|--go] <vX.Y.Z[-alpha.N|-beta.N|-rc.N]> [sdk-go/go.mod (--go only)]" >&2
  exit 2
}

# parse <input> → sets MAJOR MINOR PATCH PRE_KIND PRE_NUM (the last two empty
# for a stable version); returns 1 on anything that is not a release version.
parse() {
  local num='(0|[1-9][0-9]*)'
  local re="^v?${num}\.${num}\.${num}(-(alpha|beta|rc)\.${num})?$"
  [[ "$1" =~ $re ]] || return 1
  MAJOR="${BASH_REMATCH[1]}"
  MINOR="${BASH_REMATCH[2]}"
  PATCH="${BASH_REMATCH[3]}"
  PRE_KIND="${BASH_REMATCH[5]:-}"
  PRE_NUM="${BASH_REMATCH[6]:-}"
}

# pep440 — the PEP 440 spelling of the parsed version.
pep440() {
  local base="${MAJOR}.${MINOR}.${PATCH}"
  case "$PRE_KIND" in
    "")    echo "$base" ;;
    alpha) echo "${base}a${PRE_NUM}" ;;
    beta)  echo "${base}b${PRE_NUM}" ;;
    rc)    echo "${base}rc${PRE_NUM}" ;;
  esac
}

# semver — the SemVer spelling of the parsed version (the tag minus `v`).
semver() {
  local base="${MAJOR}.${MINOR}.${PATCH}"
  if [ -n "$PRE_KIND" ]; then echo "${base}-${PRE_KIND}.${PRE_NUM}"; else echo "$base"; fi
}

mode=full
case "${1:-}" in
  --pep440) mode=pep440; shift ;;
  --go) mode=go; shift ;;
  -h|--help) usage ;;
  --*) usage ;;
esac
if [ "$mode" = go ]; then [ $# -eq 2 ] || usage
else [ $# -eq 1 ] || usage
fi
input="$1"

if ! parse "$input"; then
  echo "sdk-version: '${input}' is not a release version — expected vX.Y.Z, optionally -alpha.N, -beta.N or -rc.N, no leading zeros" >&2
  exit 1
fi

if [ "$mode" = pep440 ]; then
  pep440
  exit 0
fi

version="$(semver)"
if [ "$mode" = go ]; then
  case "$MAJOR" in
    0|1) echo "::error::Go SDK releases require major >= 2; sdk-go/v0.* or sdk-go/v1.* would break the 0.x Go module" >&2; exit 1 ;;
  esac
  if [ ! -f "$2" ]; then
    echo "::error::source_ref has no sdk-go/go.mod; untick publish_go when publishing a source commit from before the Go SDK" >&2
    exit 1
  fi
  module="go.daytona.com/sdk/v${MAJOR}"
  # Require one declaration with exactly this path; do not run go or source code.
  if ! declared=$(awk '$1 == "module" { if (NF != 2) exit 1; print $2 }' "$2") || [ "$declared" != "$module" ]; then
    echo "::error::source sdk-go/go.mod must declare exactly module ${module} (release major ${MAJOR} must match its /vMAJOR suffix)" >&2
    exit 1
  fi
  printf 'go=v%s\ngo_tag=sdk-go/v%s\ngo_module=%s\n' "$version" "$version" "$module"
  exit 0
fi
printf 'version=%s\n' "$version"
printf 'npm=%s\n' "$version"
printf 'cli=%s\n' "$version"
printf 'cli_tag=v%s\n' "$version"
printf 'pypi=%s\n' "$(pep440)"
