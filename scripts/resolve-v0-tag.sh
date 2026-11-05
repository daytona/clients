#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Print the daytona/clients release tag the 2.x SDKs vendor as their v0
# namespace (`Daytona().v0`): the 0.x SDK at a PINNED tag AND commit.
#
# The pin lives in exactly one place, scripts/v0-sdk-version, two lines:
#
#   v0.224.0                                   <- the release tag (vX.Y.Z)
#   9fef984e676710a53a232d47f763d729dc5e6d14   <- the commit the tag peels to
#
# The commit (`git rev-parse <tag>^{commit}`, what a clone's HEAD is) makes
# the pin immutable: a force-moved tag fails --verify, bundle-v0.sh and the
# publish gate instead of silently shipping different third-party code.
# Bumping the pin is a two-line PR plus a 2.x release. Releases always use
# the pin.
#
# DAYTONA_V0_TAG=<tag> overrides the pin for manual testing only (the publish
# workflow's `v0_tag` input sets it, and the gate refuses to publish to PyPI or
# npm with it unless `allow_unpinned_v0` is set); it must have the same vX.Y.Z
# shape. An override equal to the pinned tag keeps the pinned commit check; for
# another tag no commit is enforced unless DAYTONA_V0_SHA is set.
#
#   resolve-v0-tag.sh            print the tag (pin, or the override)
#   resolve-v0-tag.sh --sha      print the pinned commit ('' for an override
#                                without DAYTONA_V0_SHA)
#   resolve-v0-tag.sh --verify   print the tag after checking that it exists in
#                                daytona/clients and, when a commit is pinned,
#                                that the tag still peels to it
#                                (DAYTONA_V0_REPO overrides the clone URL)
set -euo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# DAYTONA_V0_PIN_FILE reads another checkout's pin (the publish gate runs
# main's copy of this script against the pin of the release being published).
pin_file=${DAYTONA_V0_PIN_FILE:-"$here/v0-sdk-version"}
repo=${DAYTONA_V0_REPO:-https://github.com/daytona/clients.git}
shape='^v[0-9]+\.[0-9]+\.[0-9]+$'
sha_shape='^[0-9a-f]{40}$'

mode=tag
case "${1:-}" in
  "") ;;
  --verify) mode=verify ;;
  --sha) mode=sha ;;
  *) echo "usage: resolve-v0-tag.sh [--verify|--sha]" >&2; exit 2 ;;
esac

if [ -n "${DAYTONA_V0_TAG:-}" ]; then
  tag=$DAYTONA_V0_TAG
  sha=${DAYTONA_V0_SHA:-}
  source="DAYTONA_V0_TAG override (testing only; releases use $pin_file)"
  # An override may change the tag; it never drops verification of the pinned
  # one. Naming the pinned tag always checks the pin file's commit, and a
  # conflicting DAYTONA_V0_SHA is refused rather than trusted.
  if [ -f "$pin_file" ] && [ "$tag" = "$(sed -n '1p' "$pin_file" | sed -e 's/\r$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')" ]; then
    pin_sha=$(sed -n '2p' "$pin_file" | sed -e 's/\r$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    if [ -n "$sha" ] && [ "$sha" != "$pin_sha" ]; then
      echo "resolve-v0-tag: DAYTONA_V0_SHA=$sha conflicts with the commit pinned for $tag in $pin_file ($pin_sha)" >&2
      exit 1
    fi
    sha=$pin_sha
  fi
else
  [ -f "$pin_file" ] || { echo "resolve-v0-tag: $pin_file is missing" >&2; exit 1; }
  # Line 1 the tag, line 2 the commit; trailing blank lines are fine, any
  # further non-blank line is refused rather than silently ignored.
  extra=$(sed -n '3,$p' "$pin_file" | sed -e 's/\r$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
  if [ -n "$extra" ]; then
    echo "resolve-v0-tag: $pin_file must hold exactly two lines (tag, commit); found more" >&2
    exit 1
  fi
  tag=$(sed -n '1p' "$pin_file" | sed -e 's/\r$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
  sha=$(sed -n '2p' "$pin_file" | sed -e 's/\r$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
  source=$pin_file
  if ! printf '%s\n' "$sha" | grep -Eq "$sha_shape"; then
    echo "resolve-v0-tag: line 2 of $pin_file must be the tag's full 40-character commit sha (got '$sha')" >&2
    exit 1
  fi
fi

# Reject CR/LF first: grep matches per line, so a multi-line value would pass
# the shape check and later forge extra $GITHUB_OUTPUT records.
case $tag in *$'\n'* | *$'\r'*) echo "resolve-v0-tag: the v0 tag must be a single line" >&2; exit 1 ;; esac
if ! printf '%s\n' "$tag" | grep -Eq "$shape"; then
  echo "resolve-v0-tag: '$tag' (from $source) is not a vX.Y.Z release tag" >&2
  exit 1
fi

if [ "$mode" = sha ]; then
  echo "$sha"
  exit 0
fi

if [ "$mode" = verify ]; then
  refs=$(git ls-remote --tags "$repo" "refs/tags/$tag" "refs/tags/$tag^{}")
  if [ -z "$refs" ]; then
    echo "resolve-v0-tag: tag $tag (from $source) does not exist in $repo" >&2
    exit 1
  fi
  # An annotated tag lists its peeled commit as <tag>^{}; a lightweight tag
  # points at the commit directly.
  peeled=$(printf '%s\n' "$refs" | awk -v t="refs/tags/$tag^{}" '$2 == t {print $1}')
  [ -n "$peeled" ] || peeled=$(printf '%s\n' "$refs" | awk -v t="refs/tags/$tag" '$2 == t {print $1}')
  if [ -n "$sha" ] && [ "$peeled" != "$sha" ]; then
    echo "resolve-v0-tag: tag $tag now points at $peeled, not the pinned $sha (from $source); was the tag moved?" >&2
    exit 1
  fi
fi

echo "$tag"
