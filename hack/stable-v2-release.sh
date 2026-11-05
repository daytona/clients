#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Print the tag of the first stable 2.x (or later) release of this repository,
# such as v2.0.0, or nothing while 2.x has only prereleases (v2.0.0-alpha.7).
# A stable release is a published GitHub release, neither a draft nor marked as
# a prerelease, tagged vX.Y.Z with no suffix: the releases that the "Latest"
# re-pin in release.yaml chooses from.
#
# Until the first stable 2.x release exists, the `daytona` Homebrew formula still
# follows the 0.x line: release/v0 also uploads the 0.x binaries as
# daytona-<os>-<arch> assets (release.yaml) and dispatches the `daytona` formula
# update next to `daytona@0` (sdk_publish.yaml). See PUBLISHING.md.
#
# Usage:
#   stable-v2-release.sh                   read the releases of GITHUB_REPOSITORY
#                                          (default daytona/clients) with gh
#   stable-v2-release.sh --tags-from FILE  read the tags of stable releases, one
#                                          per line, from FILE (- for stdin), for
#                                          dry runs
#
# Exits non-zero when the releases cannot be read, so that a GitHub API failure
# is never taken for "no stable 2.x release": that would move the `daytona`
# formula back to 0.x.

set -euo pipefail

repo="${GITHUB_REPOSITORY:-daytona/clients}"

case "${1:-}" in
  "")
    if ! tags=$(gh release list --repo "$repo" --limit 1000 --exclude-drafts --exclude-pre-releases --json tagName --jq '.[].tagName'); then
      echo "::error::Could not list the releases of ${repo}" >&2
      exit 1
    fi
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
