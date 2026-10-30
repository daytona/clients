#!/usr/bin/env sh
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: AGPL-3.0
#
# Install the Daytona 0.x CLI as `daytona-v0` (Linux/macOS), next to the 2.x
# `daytona` CLI.
#
#   curl -fsSL https://github.com/daytona/clients/releases/download/v0.X.Y/install-v0.sh | sh
#   … | sh -s -- --version 0.X.Y                         # a specific 0.x release
#   … | DAYTONA_INSTALL_DIR=~/.local/bin sh              # a custom directory
#
# Downloads the daytona-v0-<os>-<arch> asset of the newest stable 0.x release
# (or --version), verifies it against the release's SHA256SUMS, and installs it
# as `daytona-v0`. Releases from before the 0.x line was renamed only carry
# daytona-<os>-<arch> assets and cannot be installed with this script.
set -eu

REPO="${DAYTONA_RELEASE_REPO:-daytona/clients}"
VERSION="${DAYTONA_VERSION:-}"
INSTALL_DIR="${DAYTONA_INSTALL_DIR:-}"

while [ $# -gt 0 ]; do
  case "$1" in
    --version|--dir)
      [ $# -ge 2 ] || { echo "$1 requires a value" >&2; exit 2; }
      case "$1" in
        --version) VERSION="$2" ;;
        --dir) INSTALL_DIR="$2" ;;
      esac
      shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

os="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "$os" in linux|darwin) ;; *) echo "unsupported OS: $os (on Windows, download daytona-v0-windows-<arch>.exe from the release)" >&2; exit 1 ;; esac
arch="$(uname -m)"
case "$arch" in
  x86_64|amd64) arch=amd64 ;;
  aarch64|arm64) arch=arm64 ;;
  *) echo "unsupported architecture: $arch" >&2; exit 1 ;;
esac

if [ -z "$VERSION" ]; then
  # The repository releases the 2.x line too, so "Latest" is not the newest
  # 0.x release: take the highest stable v0.X.Y tag.
  VERSION="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases?per_page=100" \
    | tr ',' '\n' | sed -n 's/.*"tag_name": *"v\(0\.[0-9]*\.[0-9]*\)".*/\1/p' \
    | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1)"
  [ -n "$VERSION" ] || { echo "could not determine the newest 0.x release from GitHub; pass --version 0.X.Y" >&2; exit 1; }
fi
VERSION="${VERSION#v}"
case "$VERSION" in 0.*) ;; *) echo "this script installs the 0.x CLI only (got $VERSION)" >&2; exit 1 ;; esac

base="https://github.com/${REPO}/releases/download/v${VERSION}"
asset="daytona-v0-${os}-${arch}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "downloading daytona-v0 ${VERSION} (${os}/${arch})"
curl -fsSL -o "$tmp/$asset" "$base/$asset" \
  || { echo "release v${VERSION} has no $asset asset (releases from before the daytona-v0 rename cannot be installed with this script)" >&2; exit 1; }
curl -fsSL -o "$tmp/SHA256SUMS" "$base/SHA256SUMS"
expected="$(grep " ${asset}\$" "$tmp/SHA256SUMS" | cut -d' ' -f1)"
[ -n "$expected" ] || { echo "SHA256SUMS has no entry for $asset" >&2; exit 1; }
if command -v sha256sum >/dev/null 2>&1; then actual="$(sha256sum "$tmp/$asset" | cut -d' ' -f1)"
else actual="$(shasum -a 256 "$tmp/$asset" | cut -d' ' -f1)"; fi
[ "$expected" = "$actual" ] || { echo "checksum mismatch for $asset" >&2; exit 1; }
chmod +x "$tmp/$asset"

if [ -z "$INSTALL_DIR" ]; then
  if [ -w /usr/local/bin ]; then INSTALL_DIR=/usr/local/bin
  else INSTALL_DIR="$HOME/.local/bin"; fi
fi
mkdir -p "$INSTALL_DIR"
mv "$tmp/$asset" "$INSTALL_DIR/daytona-v0"
echo "installed $INSTALL_DIR/daytona-v0"
case ":$PATH:" in *":$INSTALL_DIR:"*) ;; *) echo "note: $INSTALL_DIR is not on your PATH" ;; esac
"$INSTALL_DIR/daytona-v0" version
