#!/usr/bin/env sh
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: AGPL-3.0

# Install the daytona CLI (Linux/macOS).
#
#   curl -fsSL https://github.com/daytona/clients/releases/download/v2.0.0-alpha.3/install.sh | sh
#   … | DAYTONA_INSTALL_DIR=~/.local/bin sh      # a custom directory
#
# Downloads the release binary for this OS/arch from GitHub Releases,
# verifies it against the release's SHA256SUMS, and installs it as `daytona`.
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
case "$os" in linux|darwin) ;; *) echo "unsupported OS: $os (use install.ps1 on Windows)" >&2; exit 1 ;; esac
arch="$(uname -m)"
case "$arch" in
  x86_64|amd64) arch=amd64 ;;
  aarch64|arm64) arch=arm64 ;;
  *) echo "unsupported architecture: $arch" >&2; exit 1 ;;
esac

if [ -z "$VERSION" ]; then
	# The clients repo carries the V0 stable line and SDK releases beside the
	# V2 CLI alphas, so "newest release" is not "newest CLI": pick the newest
	# tag of the CLI's own 2.x line (`v2.*`), which is the only line that ships
	# the daytona-<os>-<arch> assets.
	# GitHub returns newest first; one tag_name per line, first v2.* wins.
	VERSION="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases?per_page=100" \
		| tr ',' '\n' | sed -n 's/.*"tag_name": *"\(v2\.[^"]*\)".*/\1/p' | sed -n '1p')"
	[ -n "$VERSION" ] || { echo "could not determine the newest 2.x CLI release from GitHub; pass --version X.Y.Z-alpha.N" >&2; exit 1; }
fi
VERSION="${VERSION#v}"

base="https://github.com/${REPO}/releases/download/v${VERSION}"
asset="daytona-${os}-${arch}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "downloading daytona ${VERSION} (${os}/${arch})"
curl -fsSL -o "$tmp/$asset" "$base/$asset"
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
mv "$tmp/$asset" "$INSTALL_DIR/daytona"
echo "installed $INSTALL_DIR/daytona"
case ":$PATH:" in *":$INSTALL_DIR:"*) ;; *) echo "note: $INSTALL_DIR is not on your PATH" ;; esac
"$INSTALL_DIR/daytona" version
