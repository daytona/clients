#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Tests for scripts/go-vanity-site.sh: the go-import prefix must be the vanity
# domain itself (a different prefix makes `go get` reject the module), and
# 404.html must carry it too, since Pages serves 404.html for every module path.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

bash "$here/go-vanity-site.sh" "$tmp/site" >/dev/null
want='<meta name="go-import" content="go.daytona.com git https://github.com/daytona/clients">'
for page in index.html 404.html; do
  grep -qxF "$want" "$tmp/site/$page" || fail "$page lacks: $want"
  grep -qF 'https://github.com/daytona/clients/tree/main{/dir}' "$tmp/site/$page" || fail "$page go-source does not point at main"
done
[ "$(cat "$tmp/site/CNAME")" = go.daytona.com ] || fail "CNAME is not go.daytona.com"
[ -f "$tmp/site/.nojekyll" ] || fail ".nojekyll missing"

DOMAIN=go.example.test BRANCH=dev bash "$here/go-vanity-site.sh" "$tmp/other" >/dev/null
grep -qF 'content="go.example.test git https://github.com/daytona/clients"' "$tmp/other/404.html" || fail "DOMAIN not applied"
grep -qF '/tree/dev{/dir}' "$tmp/other/index.html" || fail "BRANCH not applied"

echo "go-vanity-site: all tests passed"
