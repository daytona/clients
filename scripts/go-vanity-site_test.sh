#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Tests for scripts/go-vanity-site.sh: the go-import tag must name the module
# prefix and the sdk-go subdirectory (a different prefix makes `go get` reject
# the module), and 404.html must carry it too, since Pages serves 404.html for
# every package path.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

# The default-case assertions must not see overrides from the caller's environment.
unset DOMAIN PREFIX SUBDIR REPO BRANCH

bash "$here/go-vanity-site.sh" "$tmp/site" >/dev/null
want='<meta name="go-import" content="go.daytona.com/sdk git https://github.com/daytona/clients sdk-go">'
for page in index.html 404.html; do
  grep -qxF "$want" "$tmp/site/$page" || fail "$page lacks: $want"
  grep -qF 'https://github.com/daytona/clients/tree/main/sdk-go{/dir}' "$tmp/site/$page" || fail "$page go-source does not point at main/sdk-go"
done
[ "$(cat "$tmp/site/CNAME")" = go.daytona.com ] || fail "CNAME is not go.daytona.com"
[ -f "$tmp/site/.nojekyll" ] || fail ".nojekyll missing"

DOMAIN=go.example.test BRANCH=dev bash "$here/go-vanity-site.sh" "$tmp/other" >/dev/null
grep -qF 'content="go.example.test/sdk git https://github.com/daytona/clients sdk-go"' "$tmp/other/404.html" || fail "DOMAIN not applied"
grep -qF '/tree/dev/sdk-go{/dir}' "$tmp/other/index.html" || fail "BRANCH not applied"

bash "$here/go-vanity-site.sh" "$tmp/site" >/dev/null || fail "regenerating over an earlier output failed"
mkdir -p "$tmp/unrelated" && echo keep >"$tmp/unrelated/file"
if bash "$here/go-vanity-site.sh" "$tmp/unrelated" >/dev/null 2>&1; then fail "overwrote an unrelated directory"; fi
[ "$(cat "$tmp/unrelated/file")" = keep ] || fail "unrelated directory was modified"

echo "go-vanity-site: all tests passed"
