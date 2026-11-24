#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Generate the static site that serves the Go vanity import path of the 2.x Go
# SDK, go.daytona.com/sdk/v2. GitHub Pages serves it;
# .github/workflows/go-vanity-pages.yaml deploys it from main.
#
#   scripts/go-vanity-site.sh [OUT_DIR]      # default: ./go-vanity-site
#
# `go get go.daytona.com/sdk/v2` fetches https://go.daytona.com/sdk/v2?go-get=1
# and reads the go-import meta tag: the prefix go.daytona.com/sdk is the
# directory sdk-go/ of daytona/clients (the fourth, subdirectory field, read by
# Go 1.25 and later), and the module's versions are the tags sdk-go/vX.Y.Z.
# Pages answers every path that has no file with 404.html, so 404.html carries
# the same tags as index.html.
#
# The 0.x Go modules keep their github.com/daytona/clients/... paths and never
# read this site.
#
# Env: DOMAIN (default go.daytona.com), PREFIX (default sdk), SUBDIR
#      (default sdk-go), REPO (default daytona/clients), BRANCH for the
#      go-source links (default main).
set -euo pipefail

domain=${DOMAIN:-go.daytona.com}
prefix="${domain}/${PREFIX:-sdk}"
subdir=${SUBDIR:-sdk-go}
repo=${REPO:-daytona/clients}
branch=${BRANCH:-main}
out=${1:-go-vanity-site}
repo_url="https://github.com/${repo}"

render() {
  cat <<HTML
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="go-import" content="${prefix} git ${repo_url} ${subdir}">
<meta name="go-source" content="${prefix} ${repo_url} ${repo_url}/tree/${branch}/${subdir}{/dir} ${repo_url}/blob/${branch}/${subdir}{/dir}/{file}#L{line}">
<meta http-equiv="refresh" content="0; url=${repo_url}/tree/${branch}/${subdir}">
</head>
<body>
The Go module <code>${prefix}</code> lives in <a href="${repo_url}/tree/${branch}/${subdir}">${subdir}/</a> of ${repo_url}.
</body>
</html>
HTML
}

# Only replace an earlier output of this script (marked by .nojekyll), never
# an unrelated directory passed by mistake.
if [ -e "$out" ] && [ -n "$(ls -A "$out" 2>/dev/null)" ] && [ ! -f "$out/.nojekyll" ]; then
  echo "go-vanity-site: $out exists and is not a generated site; refusing to overwrite it" >&2
  exit 1
fi
rm -rf "$out"
mkdir -p "$out"
render >"$out/index.html"
render >"$out/404.html"
: >"$out/.nojekyll"
printf '%s\n' "$domain" >"$out/CNAME"
echo "go-vanity-site: wrote $out for $prefix -> $repo_url $subdir"
