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
  echo "usage: $(basename "$0") [--pep440] <vX.Y.Z[-alpha.N|-beta.N|-rc.N]>" >&2
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
  -h|--help) usage ;;
  --*) usage ;;
esac
[ $# -eq 1 ] || usage
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
printf 'version=%s\n' "$version"
printf 'npm=%s\n' "$version"
printf 'cli=%s\n' "$version"
printf 'cli_tag=v%s\n' "$version"
printf 'pypi=%s\n' "$(pep440)"
