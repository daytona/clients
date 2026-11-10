#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Fail if anything the publish-time v0 bundle produces was committed
# (the v0 namespace is only ever copied from a
# daytona/clients release tag by scripts/bundle-v0.sh, never by hand).
#
# Checks the git index, so a .gitignore cannot hide a force-added file:
#   - nothing tracked under sdk-python/src/daytona/v0/ or sdk-typescript/src/v0/;
#   - the Python `v0-sdk` extra is committed empty (the bundle fills it with pins);
#   - the TypeScript manifest carries none of v0's generated API clients.
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
status=0
mode=${1:-}

# Case-insensitively: a force-added daytona/V0/ or src/V0/ would ship too, and
# shadows the real path on a case-insensitive checkout.
tracked=$(git ls-files -- sdk-python/src/daytona sdk-typescript/src | grep -iE '^(sdk-python/src/daytona|sdk-typescript/src)/v0($|/)' || true)
if [ -n "$tracked" ]; then
  echo "::error::bundled v0 files are committed (they are produced by scripts/bundle-v0.sh at publish time):"
  printf '%s\n' "$tracked" | sed 's/^/  /'
  status=1
fi

# Read each file into a variable before grepping it: `git show … | grep -q`
# under pipefail reports a match as a failure once the file outgrows the pipe
# buffer (grep exits early, git show gets SIGPIPE), so a large bundled
# package-lock.json would pass.
pyproject=$(git show :./sdk-python/pyproject.toml)
if ! grep -qx 'v0-sdk = \[\]' <<<"$pyproject"; then
  echo "::error::sdk-python/pyproject.toml must commit the v0-sdk extra empty ('v0-sdk = []'); bundle-v0.sh fills it at publish time"
  status=1
fi

for manifest in sdk-typescript/package.json sdk-typescript/package-lock.json; do
  content=$(git show ":./$manifest")
  if grep -Eq '"(node_modules/(.*/node_modules/)?)?@daytona/(api-client|toolbox-api-client|analytics-api-client)"' <<<"$content"; then
    echo "::error::$manifest carries v0's API clients; bundle-v0.sh adds them at publish time"
    status=1
  fi
done

# The bundle also merges v0's own runtime dependencies into package.json. A
# hand-edited bundled manifest with the API clients removed would still carry
# them, so check the declared (root) dependencies for v0-only names too. These
# are the packages bundle-v0.mjs merges at v0.231.1 that v2 does not use
# (form-data is merged too, but v2 also resolves it). sdk-ci's "v0 bundle from
# the publish snapshot" job runs `--check-list` on a real bundle, so a pin
# bump that merges a new name fails there until it is listed here.
v0_only='@aws-sdk/client-s3 @aws-sdk/lib-storage @iarna/toml @opentelemetry/exporter-trace-otlp-http @opentelemetry/instrumentation-http @opentelemetry/otlp-exporter-base @opentelemetry/resources @opentelemetry/sdk-node @opentelemetry/sdk-trace @opentelemetry/semantic-conventions @types/ws busboy expand-tilde isomorphic-ws pathe shell-quote socket.io-client tar tinyglobby tslib'
# `--check-list`, run on a freshly bundled tree: every dependency the bundle
# added to package.json (vs the committed one) must be on v0_only, one of v0's
# API clients, or a name v2 also resolves.
if [ "$mode" = --check-list ]; then
  added=$(V0_ONLY="$v0_only" python3 -c '
import json, os, subprocess
sections = ("dependencies", "optionalDependencies", "peerDependencies")
def names(data):
    return {n for s in sections for n in (data.get(s) or {})}
committed = json.loads(subprocess.run(["git", "show", "HEAD:./sdk-typescript/package.json"], capture_output=True, text=True, check=True).stdout)
bundled = json.load(open("sdk-typescript/package.json"))
known = set(os.environ["V0_ONLY"].split()) | {"@daytona/api-client", "@daytona/toolbox-api-client", "@daytona/analytics-api-client", "form-data"}
print(" ".join(sorted(names(bundled) - names(committed) - known)))
')
  if [ -n "$added" ]; then
    echo "::error::the v0 bundle merges dependencies missing from v0_only in check-no-bundled-v0.sh: $added"
    exit 1
  fi
  echo "v0_only covers every dependency the bundle merges"
  exit 0
fi

for manifest in sdk-typescript/package.json sdk-typescript/package-lock.json; do
  found=$(git show ":./$manifest" | V0_ONLY="$v0_only" python3 -c '
import json, os, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    sys.exit(0)  # not JSON: the API-client grep above still covers this file
root = data.get("packages", {}).get("", data)  # the lockfile root entry, or package.json itself
declared = set()
for section in ("dependencies", "optionalDependencies", "peerDependencies"):  # what bundle-v0.mjs merges into; devDependencies may legitimately overlap
    declared |= set(root.get(section) or {})
print(" ".join(sorted(declared & set(os.environ["V0_ONLY"].split()))))
')
  if [ -n "$found" ]; then
    echo "::error::$manifest declares v0-only dependencies ($found); bundle-v0.sh adds them at publish time"
    status=1
  fi
done

[ "$status" -eq 0 ] && echo "no bundled v0 content is committed"
exit "$status"
