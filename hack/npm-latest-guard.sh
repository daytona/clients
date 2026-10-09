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
# since `latest` tracks stable versions only. PACKAGE defaults to
# @daytona/sdk; every npm package of the repository shares its version.

set -euo pipefail

mode="${1:?usage: npm-latest-guard.sh check <version> | verify}"
version="${2:-}"
pkg="${PACKAGE:-@daytona/sdk}"
registry=https://registry.npmjs.org/

run_guard() {
  local versions latest
  versions=$(npm view "$pkg" versions --json --registry "$registry")
  latest=$(npm view "$pkg" dist-tags.latest --registry "$registry")
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
const parsed = [].concat(JSON.parse(versionsJson)).map((v) => ({ v, p: parse(v) })).filter((x) => x.p);
const fail = (msg) => {
  console.log(`::error::${msg}`);
  process.exit(1);
};

if (mode === 'check') {
  const next = parse(version);
  if (!next || next.pre) fail(`Only stable X.Y.Z versions may take the npm latest tag from a release branch (got ${version})`);
  const blocker = parsed.filter((x) => !x.p.pre && compare(x.p, next) >= 0).sort((a, b) => compare(b.p, a.p))[0];
  if (blocker) fail(`${pkg}@${blocker.v} is already published; ${version} with --tag latest would move latest backwards. Use a line tag such as v${next.core[0]}-latest.`);
  console.log(`${version} is above every published stable version of ${pkg}; latest may move to it.`);
} else if (mode === 'verify') {
  const highest = parsed.filter((x) => !x.p.pre).sort((a, b) => compare(b.p, a.p))[0];
  if (highest && highest.v !== latest) {
    fail(`npm latest of ${pkg} is ${latest} but ${highest.v} is published. Run 'npm dist-tag add <package>@${highest.v} latest' for every npm package of this repository.`);
  }
  console.log(`npm latest of ${pkg} is ${latest}, the highest stable version.`);
} else {
  fail(`Unknown mode: ${mode}`);
}
EOF
}

if [ "$mode" != verify ]; then
  run_guard
  exit
fi

# The registry can take a few seconds to show a fresh publish.
for attempt in 1 2 3 4 5 6; do
  if run_guard; then
    exit 0
  fi
  [ "$attempt" -lt 6 ] && sleep 10
done
exit 1
