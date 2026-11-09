#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# `npm publish` the package in the current directory under $NPM_TAG, skipping
# a version that is already on the registry. npm refuses to publish over an
# existing version, so without the skip a publish that stopped halfway (e.g.
# a transient registry or Sigstore error) cannot be completed by re-running
# the job: the packages published before the failure would fail it again.
#
# Usage (in the package directory): NPM_TAG=latest bash npm-publish.sh

set -euo pipefail

: "${NPM_TAG:?NPM_TAG must be set}"
registry=https://registry.npmjs.org/

name=$(node -p 'require("./package.json").name')
version=$(node -p 'require("./package.json").version')

# Only a 200 skips and only a 404 publishes; an unreachable or erroring
# registry fails rather than guessing either way. The query string keeps a
# cached 404 from sticking.
status=$(curl -sS --max-time 60 -o /dev/null -w '%{http_code}' \
  "${registry}${name/\//%2f}/${version}?nocache=$(date +%s%N)" || true)
case "$status" in
  200)
    echo "::notice::${name}@${version} is already on npm, skipping the publish"
    exit 0
    ;;
  404) ;;
  *)
    echo "::error::Could not tell whether ${name}@${version} is on npm (registry answered HTTP ${status:-000})"
    exit 1
    ;;
esac

npm publish --tag "$NPM_TAG" --access public --registry "$registry"
