#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Bundle the v0 SDK (daytona/clients, the 0.x SDK at the pinned tag) into the
# 2.x SDKs as their `v0` namespace: Python `daytona.v0`, TypeScript `@daytona/sdk/v0`.
#
#   scripts/bundle-v0.sh [--lang python|typescript|all] [--src DIR] [TAG]
#   scripts/bundle-v0.sh --clean
#
# TAG defaults to the pin in scripts/v0-sdk-version (read through
# scripts/resolve-v0-tag.sh). Passing TAG or setting DAYTONA_V0_TAG
# overrides it for testing only; releases use the pin. --src uses an existing checkout of
# daytona/clients at that tag instead of cloning one. The publish workflow runs
# this at publish time; locally it is the same command. The bundled trees
# (sdk-python/src/daytona/v0/, sdk-typescript/src/v0/) and the manifest
# edits it makes (the Python `v0-sdk` extra, the TypeScript dependencies) are never
# committed — scripts/check-no-bundled-v0.sh fails CI if they are.
# --clean removes the bundles and empties the Python `v0-sdk` extra; restore
# sdk-typescript/package.json and package-lock.json with git yourself.
set -euo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
clients=$(dirname -- "$here")
lang=all
src=""
src_given=""
tag=""

while [ $# -gt 0 ]; do
  case "$1" in
    --lang) lang=${2:?--lang needs python, typescript or all}; shift 2 ;;
    --src) src=${2:?--src needs a directory}; src_given=1; shift 2 ;;
    --clean)
      python3 "$here/bundle_v0_python.py" --clean
      rm -rf "$clients/sdk-typescript/src/v0"
      echo "bundle-v0: removed $clients/sdk-typescript/src/v0 (restore sdk-typescript/package.json + package-lock.json with git)"
      exit 0 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    -*) echo "bundle-v0: unknown option $1" >&2; exit 2 ;;
    *) tag=$1; shift ;;
  esac
done
case "$lang" in python|typescript|all) ;; *) echo "bundle-v0: --lang must be python, typescript or all" >&2; exit 2 ;; esac

cloned_src=""
started=""
bundled_ok=""
ts_manifest_backup=$(mktemp)
cleanup() {
  status=$?
  if [ -n "$started" ] && [ -z "$bundled_ok" ]; then
    echo "bundle-v0: failed; removing the partial bundle" >&2
    if [ "$lang" = python ] || [ "$lang" = all ]; then python3 "$here/bundle_v0_python.py" --clean >/dev/null 2>&1 || true; fi
    if [ "$lang" = typescript ] || [ "$lang" = all ]; then
      rm -rf "$clients/sdk-typescript/src/v0"
      cp "$ts_manifest_backup" "$clients/sdk-typescript/package.json"
    fi
  fi
  rm -f "$ts_manifest_backup"
  if [ -n "$cloned_src" ]; then rm -rf "$cloned_src"; fi
  exit "$status"
}
trap cleanup EXIT

if [ -z "$tag" ]; then
  tag=$("$here/resolve-v0-tag.sh")
  sha=$("$here/resolve-v0-tag.sh" --sha)
elif [ "$tag" = "$("$here/resolve-v0-tag.sh")" ]; then
  # The pinned tag passed explicitly (the publish jobs): its commit is pinned too.
  sha=$("$here/resolve-v0-tag.sh" --sha)
else
  tag=$(DAYTONA_V0_TAG="$tag" "$here/resolve-v0-tag.sh")
  sha=${DAYTONA_V0_SHA:-}
fi
version=${tag#v}
echo "bundle-v0: v0 namespace from daytona/clients@$tag"

# The pin itself, regardless of overrides, to compare against.
pin_tag=$(env -u DAYTONA_V0_TAG "$here/resolve-v0-tag.sh" 2>/dev/null || true)
pin_sha=$(env -u DAYTONA_V0_TAG "$here/resolve-v0-tag.sh" --sha 2>/dev/null || true)
if [ -n "${DAYTONA_V0_TAG:-}" ] || [ "$tag" != "$pin_tag" ]; then
  echo "bundle-v0: WARNING: v0 tag $tag overrides the pin ($pin_tag in scripts/v0-sdk-version):" \
    "this bundle is UNVERIFIED testing output — never publish it" >&2
fi

if [ -z "$src" ]; then
  src=$(mktemp -d)
  cloned_src=$src
  git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$tag" \
    "${DAYTONA_V0_REPO:-https://github.com/daytona/clients.git}" "$src"
fi

# The pin records the tag's commit: refuse a moved tag, a modified checkout
# or (for a pinned run) a --src that is not a git checkout at all — the bundle
# would carry the pinned tag's label over unverified code. The publish build
# jobs bundle the gate-verified snapshot (not a git checkout) and say so with
# DAYTONA_V0_SRC_VERIFIED=1.
pinned_run=""
[ -n "$sha" ] && pinned_run=1
if git -C "$src" rev-parse --git-dir >/dev/null 2>&1; then
  head=$(git -C "$src" rev-parse HEAD)
  dirty=""
  [ -n "$(git -C "$src" status --porcelain)" ] && dirty=1  # untracked files too: the bundlers copy the tree
  if [ -n "$src_given" ]; then
    echo "bundle-v0: --src $src is at $head${dirty:+ (dirty: uncommitted or untracked files)}"
  fi
  if [ -n "$pinned_run" ] && [ "$head" != "$sha" ]; then
    echo "bundle-v0: daytona/clients@$tag is at $head, not the pinned $sha (scripts/v0-sdk-version); was the tag moved?" >&2
    exit 1
  fi
  if [ -n "$pinned_run" ] && [ -n "$dirty" ]; then
    echo "bundle-v0: --src $src has uncommitted or untracked files; refusing to label them daytona/clients@$tag (commit, stash, or override the tag for testing)" >&2
    exit 1
  fi
  if [ -z "$pinned_run" ] && [ -n "$pin_sha" ] && [ "$head" != "$pin_sha" ]; then
    echo "bundle-v0: WARNING: --src is at $head, not the pinned commit $pin_sha: UNVERIFIED testing output" >&2
  fi
  if [ -z "$pinned_run" ] && [ -n "$dirty" ]; then
    echo "bundle-v0: WARNING: --src is dirty (uncommitted or untracked files): UNVERIFIED testing output" >&2
  fi
elif [ -n "$pinned_run" ] && [ "${DAYTONA_V0_SRC_VERIFIED:-}" != 1 ]; then
  echo "bundle-v0: --src $src is not a git checkout, so its commit cannot be checked against the pin; refusing (override the tag for testing)" >&2
  exit 1
elif [ -n "$pinned_run" ]; then
  echo "bundle-v0: --src $src is the publish gate's verified snapshot (DAYTONA_V0_SRC_VERIFIED=1)"
else
  echo "bundle-v0: WARNING: --src $src is not a git checkout: UNVERIFIED testing output" >&2
fi

# A failed run must not leave a half-written bundle behind: once bundling has
# started, remove what this run wrote (the Python bundle + its extra, the TS
# src/v0) and restore the TS manifest it edits, then fail.
cp "$clients/sdk-typescript/package.json" "$ts_manifest_backup"
started=1

if [ "$lang" = python ] || [ "$lang" = all ]; then
  python3 "$here/bundle_v0_python.py" "$src/sdk-python" "$version" "$tag"
fi
if [ "$lang" = typescript ] || [ "$lang" = all ]; then
  node "$clients/sdk-typescript/scripts/bundle-v0.mjs" "$src/sdk-typescript/src" "$version" "$tag" "$src/package.json"
  # The bundle merges v0's dependencies into package.json; they are not
  # installed yet, so `npm run build` fails on src/v0 until they are.
  echo "bundle-v0: next, in sdk-typescript: npm install (v0's dependencies), then npm run build;" \
    "--clean afterwards restores package.json (never commit the bundled manifest)"
fi
bundled_ok=1
