#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Print the first stable 2.x (or later) release tag of this repository, such as
# v2.0.0, or nothing while every 2.x tag is a prerelease (v2.0.0-alpha.7).
#
# Until the first stable 2.x release exists, the `daytona` Homebrew formula still
# follows the 0.x line: release/v0 also uploads the 0.x binaries as
# daytona-<os>-<arch> assets (release.yaml) and dispatches the `daytona` formula
# update next to `daytona@0` (sdk_publish.yaml). See PUBLISHING.md.
#
# Usage:
#   stable-v2-release.sh                   read the tags of GITHUB_REPOSITORY
#                                          (default daytona/clients) with gh
#   stable-v2-release.sh --tags-from FILE  read tag names, one per line, from
#                                          FILE (- for stdin), for dry runs
#
# Exits non-zero when the tags cannot be read, so that a GitHub API failure is
# never taken for "no stable 2.x release": that would move the `daytona`
# formula back to 0.x.

set -euo pipefail

repo="${GITHUB_REPOSITORY:-daytona/clients}"

case "${1:-}" in
  "")
    if ! refs=$(gh api --paginate "repos/${repo}/git/matching-refs/tags/v" --jq '.[].ref'); then
      echo "::error::Could not list the tags of ${repo}" >&2
      exit 1
    fi
    tags="${refs//refs\/tags\//}"
    ;;
  --tags-from)
    tags="$(cat "${2:?usage: stable-v2-release.sh [--tags-from FILE]}")"
    ;;
  *)
    echo "usage: stable-v2-release.sh [--tags-from FILE]" >&2
    exit 2
    ;;
esac

# Stable vMAJOR.MINOR.PATCH with MAJOR >= 2; a later major implies 2.x shipped.
grep -E '^v([2-9]|[1-9][0-9]+)\.[0-9]+\.[0-9]+$' <<<"$tags" | sort -V | head -n 1 || true
