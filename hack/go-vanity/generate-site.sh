#!/usr/bin/env bash
# Generate a static GitHub Pages site that serves the Go vanity import meta tag
# for `go.daytona.io/*`. Output goes to hack/go-vanity/site/.
#
# Env:
#   DOMAIN       vanity import host baked into the meta tag (default: go.daytona.io)
#   REPO         github owner/repo that holds the code (default: daytona/clients)
#   BRANCH       branch for go-source links (default: main)
#   EMIT_CNAME   "true" to write a CNAME file (only once DNS for $DOMAIN is ready).
#                Leave unset/false to serve on the default *.github.io URL for testing.
set -euo pipefail

DOMAIN="${DOMAIN:-go.daytona.io}"
REPO="${REPO:-daytona/clients}"
BRANCH="${BRANCH:-main}"
EMIT_CNAME="${EMIT_CNAME:-false}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${SCRIPT_DIR}/site"
REPO_URL="https://github.com/${REPO}"

render() {
  cat <<HTML
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="go-import" content="${DOMAIN} git ${REPO_URL}">
<meta name="go-source" content="${DOMAIN} ${REPO_URL} ${REPO_URL}/tree/${BRANCH}{/dir} ${REPO_URL}/blob/${BRANCH}{/dir}/{file}#L{line}">
<meta http-equiv="refresh" content="0; url=${REPO_URL}">
</head>
<body>
go modules for <code>${DOMAIN}</code> live at <a href="${REPO_URL}">${REPO_URL}</a>.
</body>
</html>
HTML
}

rm -rf "${OUT}"
mkdir -p "${OUT}"

# index.html: served for the import-prefix verification fetch (https://DOMAIN/?go-get=1)
render > "${OUT}/index.html"
# 404.html: GitHub Pages serves this for every unmatched path, so go's request for
# https://DOMAIN/<module>[/<subpkg>]?go-get=1 receives the meta tag (catch-all).
render > "${OUT}/404.html"
# Disable Jekyll
: > "${OUT}/.nojekyll"

if [ "${EMIT_CNAME}" = "true" ]; then
  printf '%s\n' "${DOMAIN}" > "${OUT}/CNAME"
  echo "Wrote CNAME=${DOMAIN}"
else
  echo "No CNAME written (serving on default github.io URL). Set EMIT_CNAME=true when DNS is ready."
fi

echo "Generated vanity site in ${OUT}  (DOMAIN=${DOMAIN} REPO=${REPO} BRANCH=${BRANCH})"
ls -la "${OUT}"
