#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Derive a release's channel from its version: everything sdk_publish.yaml
# does differently for an alpha, a release candidate and a stable release.
#
#   release-channel.sh 2.0.0-alpha.8     # or v2.0.0-alpha.8
#     channel=alpha
#     prerelease=true                    # the CLI's GitHub Release is a pre-release
#     npm_dist_tag=alpha                 # @daytona/sdk and @daytona/upgrade
#     homebrew_formula=daytona-alpha     # daytonaio/homebrew-cli
#     homebrew_event=update-version-alpha
#     homebrew_workflow=brew_publish_alpha.yaml
#
# | version       | channel | GitHub Release | npm tag  | Homebrew formula (event, tap workflow)                 |
# |---------------|---------|----------------|----------|--------------------------------------------------------|
# | X.Y.Z-alpha.N | alpha   | pre-release    | `alpha`  | daytona-alpha (update-version-alpha, brew_publish_alpha.yaml) |
# | X.Y.Z-rc.N    | rc      | pre-release    | `next`   | daytona-rc (update-version-rc, brew_publish_rc.yaml)   |
# | X.Y.Z         | stable  | release        | `latest` | daytona (update-version, brew_publish.yaml)            |
#
# A beta (`-beta.N`) has no channel: no Homebrew formula follows betas, so it
# is refused like any other non-release string (exit 1, nothing on stdout).
# The 0.x line keeps its own `daytona@0` formula and publishes from
# release/v0; this script does not look at the major version.
#
# Test: bash scripts/release-channel_test.sh
set -euo pipefail

[ $# -eq 1 ] || { echo "usage: $(basename "$0") <vX.Y.Z[-alpha.N|-rc.N]>" >&2; exit 2; }

num='(0|[1-9][0-9]*)'
re="^v?${num}\.${num}\.${num}(-(alpha|rc)\.${num})?$"
if ! [[ "$1" =~ $re ]]; then
  echo "release-channel: '$1' has no release channel — publish vX.Y.Z, vX.Y.Z-rc.N or vX.Y.Z-alpha.N" >&2
  exit 1
fi

case "${BASH_REMATCH[5]:-}" in
  alpha)
    printf '%s\n' channel=alpha prerelease=true npm_dist_tag=alpha \
      homebrew_formula=daytona-alpha homebrew_event=update-version-alpha homebrew_workflow=brew_publish_alpha.yaml ;;
  rc)
    printf '%s\n' channel=rc prerelease=true npm_dist_tag=next \
      homebrew_formula=daytona-rc homebrew_event=update-version-rc homebrew_workflow=brew_publish_rc.yaml ;;
  "")
    printf '%s\n' channel=stable prerelease=false npm_dist_tag=latest \
      homebrew_formula=daytona homebrew_event=update-version homebrew_workflow=brew_publish.yaml ;;
esac
