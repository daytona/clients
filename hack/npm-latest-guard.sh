#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Keep the npm `latest` dist-tag on the highest stable version when publishing
# from a release/vN maintenance branch. `npm publish --tag latest` moves the
# tag to whatever was published, even a lower version.
#
# Usage:
#   npm-latest-guard.sh check <version>  before publishing: fail unless <version>
#                                        is a stable X.Y.Z above every published
#                                        stable version
#   npm-latest-guard.sh verify           after publishing: fail unless `latest`
#                                        is the highest published stable version
#
# Versions are ordered by SemVer precedence of X.Y.Z. Prereleases never block,
# since `latest` tracks stable versions only. Every package that the
# sdk-typescript publish target releases is checked: set PACKAGES (space
# separated) to override the list. A package that has never been published
# to npm fails `check`: publish it once (from main, or with a line tag) before
# a release branch may publish it with `latest`.

set -euo pipefail

mode="${1:?usage: npm-latest-guard.sh check <version> | verify}"
version="${2:-}"
read -r -a packages <<<"${PACKAGES:-@daytona/sdk @daytona/api-client @daytona/toolbox-api-client @daytona/analytics-api-client @daytonaio/sdk @daytonaio/api-client @daytonaio/toolbox-api-client}"
registry=https://registry.npmjs.org/

run_guard() {
  local pkg="$1" versions latest
  # `|| return 1` is required: callers invoke run_guard in a condition, which
  # turns off `set -e` here, so a registry error would otherwise read as
  # "nothing published" and let the publish through.
  if ! versions=$(npm view "$pkg" versions --json --registry "$registry"); then
    echo "::error::Could not read the published versions of $pkg from the npm registry (npm view failed). If $pkg was never published, publish it to npm once before a release branch publishes it with the latest tag."
    return 1
  fi
  if ! latest=$(npm view "$pkg" dist-tags.latest --registry "$registry"); then
    echo "::error::Could not read the npm latest tag of $pkg (npm view failed)"
    return 1
  fi
  node - "$mode" "$version" "$latest" "$versions" "$pkg" <<'EOF'
const [mode, version, latest, versionsJson, pkg] = process.argv.slice(2);
const parse = (v) => {
  const m = /^(\d+)\.(\d+)\.(\d+)(-[^+]+)?(\+.*)?$/.exec(v);
  return m ? { core: m.slice(1, 4).map(Number), pre: Boolean(m[4]) } : null;
};
const compare = (a, b) => {
  for (let i = 0; i < 3; i++) if (a.core[i] !== b.core[i]) return a.core[i] - b.core[i];
  return Number(!a.pre) - Number(!b.pre);
};
const fail = (msg) => {
  console.log(`::error::${msg}`);
  process.exit(1);
};
let published;
try {
  published = JSON.parse(versionsJson);
} catch {
  fail(`Could not parse the published versions of ${pkg} from the npm registry`);
}
// npm prints a single version as a string and several as an array; anything
// else (e.g. {"error": ...} on a registry failure) means the lookup failed.
if (typeof published !== 'string' && !Array.isArray(published)) {
  fail(`Could not read the published versions of ${pkg} from the npm registry`);
}
const parsed = [].concat(published).map((v) => ({ v, p: parse(v) })).filter((x) => x.p);
if (parsed.length === 0) fail(`The npm registry returned no versions for ${pkg}`);

if (mode === 'check') {
  const next = parse(version);
  if (!next || next.pre) fail(`Only stable X.Y.Z versions may take the npm latest tag from a release branch (got ${version})`);
  const blocker = parsed.filter((x) => !x.p.pre && compare(x.p, next) >= 0).sort((a, b) => compare(b.p, a.p))[0];
  if (blocker) fail(`${pkg}@${blocker.v} is already published; ${version} with --tag latest would move latest backwards. Use a line tag such as v${next.core[0]}-latest.`);
  console.log(`${version} is above every published stable version of ${pkg}; latest may move to it.`);
} else if (mode === 'verify') {
  if (!latest) fail(`Could not read the npm latest tag of ${pkg}`);
  const highest = parsed.filter((x) => !x.p.pre).sort((a, b) => compare(b.p, a.p))[0];
  if (highest && highest.v !== latest) {
    fail(`npm latest of ${pkg} is ${latest} but ${highest.v} is published. Run 'npm dist-tag add ${pkg}@${highest.v} latest'.`);
  }
  console.log(`npm latest of ${pkg} is ${latest}, the highest stable version.`);
} else {
  fail(`Unknown mode: ${mode}`);
}
EOF
}

status=0
for pkg in "${packages[@]}"; do
  if [ "$mode" != verify ]; then
    run_guard "$pkg" || status=1
    continue
  fi
  # The registry can take a few seconds to show a fresh publish.
  for attempt in 1 2 3 4 5 6; do
    if run_guard "$pkg"; then
      break
    fi
    if [ "$attempt" -eq 6 ]; then
      status=1
    else
      sleep 10
    fi
  done
done
exit "$status"
