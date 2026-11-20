#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Generate the static site that serves the Go vanity import path
# go.daytona.com/* (the 2.x Go SDK is go.daytona.com/sdk-go/v2). GitHub Pages
# serves it; .github/workflows/go-vanity-pages.yaml deploys it from main.
#
#   scripts/go-vanity-site.sh [OUT_DIR]      # default: ./go-vanity-site
#
# `go get go.daytona.com/sdk-go/v2` fetches https://go.daytona.com/sdk-go/v2?go-get=1
# and reads the go-import meta tag. Its prefix is the bare domain, so one tag
# covers every module and package path under it: the path after the domain is
# the directory in daytona/clients, and a module's versions are the tags
# `<dir>/vX.Y.Z` (sdk-go/v2.1.0). Pages answers every path that has no file
# with 404.html, so 404.html carries the same tags as index.html.
#
# The 0.x Go modules keep their github.com/daytona/clients/... paths and never
# read this site.
#
# Env: DOMAIN (default go.daytona.com), REPO (default daytona/clients),
#      BRANCH for the go-source links (default main).
set -euo pipefail

domain=${DOMAIN:-go.daytona.com}
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
<meta name="go-import" content="${domain} git ${repo_url}">
<meta name="go-source" content="${domain} ${repo_url} ${repo_url}/tree/${branch}{/dir} ${repo_url}/blob/${branch}{/dir}/{file}#L{line}">
<meta http-equiv="refresh" content="0; url=${repo_url}">
</head>
<body>
Go modules under <code>${domain}</code> live at <a href="${repo_url}">${repo_url}</a>.
</body>
</html>
HTML
}

rm -rf "$out"
mkdir -p "$out"
render >"$out/index.html"
render >"$out/404.html"
: >"$out/.nojekyll"
printf '%s\n' "$domain" >"$out/CNAME"
echo "go-vanity-site: wrote $out for $domain -> $repo_url"
