#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Installs the pinned oasdiff release (the OpenAPI breaking-change diff that
# scripts/breaking/breaking_changes.py runs on contract/*.json) into DIR
# (default: .oasdiff/ under the repository root, git-ignored) after checking
# its sha256, and prints the binary's path.
#
#   scripts/breaking/install-oasdiff.sh [DIR]
set -euo pipefail

OASDIFF_VERSION=1.35.0

# sha256 of the release archives, from checksums.txt in the GitHub release.
# Update together with OASDIFF_VERSION.
oasdiff_sha256() {
  case "$1" in
    linux_amd64) echo a8023002fcb2eaa7f5758148a9303cb63d33b939886df312268bc0fc7b939140 ;;
    linux_arm64) echo 1f8b5aa033cd0240ae6c53460268bcd6e578526666a4487a781ece8cef39fbdd ;;
    darwin_all) echo 964ad2c0331768c4515fc49779827094bc89a65b1d309dac28886d1c3faf305c ;;
    *) return 1 ;;
  esac
}

die() { echo "install-oasdiff: $*" >&2; exit 2; }

platform() {
  case "$(uname -s)/$(uname -m)" in
    Linux/x86_64 | Linux/amd64) echo linux_amd64 ;;
    Linux/aarch64 | Linux/arm64) echo linux_arm64 ;;
    Darwin/*) echo darwin_all ;;
    *) return 1 ;;
  esac
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

dir=${1:-"$(git rev-parse --show-toplevel)/.oasdiff"}
bin="$dir/oasdiff-$OASDIFF_VERSION"
# A binary installed earlier is reused only while it still matches the hash
# recorded when it was extracted from the verified archive.
if [ -x "$bin" ] && [ -f "$bin.sha256" ] && [ "$(sha256_of "$bin")" = "$(cat "$bin.sha256")" ]; then
  echo "$bin"
  exit 0
fi
plat=$(platform) || die "no pinned oasdiff build for $(uname -s)/$(uname -m); install oasdiff $OASDIFF_VERSION yourself and set OASDIFF"
want=$(oasdiff_sha256 "$plat")
archive="oasdiff_${OASDIFF_VERSION}_${plat}.tar.gz"
url="https://github.com/oasdiff/oasdiff/releases/download/v${OASDIFF_VERSION}/${archive}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsSL --retry 3 -o "$tmp/$archive" "$url" || die "download failed: $url"
got=$(sha256_of "$tmp/$archive")
[ "$got" = "$want" ] || die "checksum mismatch for $archive: got $got, want $want"
tar -xzf "$tmp/$archive" -C "$tmp" oasdiff
mkdir -p "$dir"
chmod 0755 "$tmp/oasdiff"
sha256_of "$tmp/oasdiff" > "$tmp/oasdiff.sha256"
mv -f "$tmp/oasdiff" "$bin"
mv -f "$tmp/oasdiff.sha256" "$bin.sha256"
echo "$bin"
