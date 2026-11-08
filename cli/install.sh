#!/usr/bin/env sh
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: AGPL-3.0

# Install the daytona CLI (Linux/macOS).
#
#   curl -fsSL https://raw.githubusercontent.com/daytona/clients/main/cli/install.sh | sh
#   … | sh -s -- --channel rc                  # highest release candidate or stable release
#   … | sh -s -- --version 2.1.0-alpha.2       # an exact version
#   … | DAYTONA_INSTALL_DIR=~/.local/bin sh    # a custom directory
#
# Without --version it installs the highest stable 2.x release (vX.Y.Z), by
# version, not by publication date. Pre-releases only come with a channel:
# `rc` is the highest release candidate or stable release, `alpha` the
# highest alpha, release candidate or stable release. Until the first stable
# 2.x release exists, the default installs the highest pre-release and says
# so; `--channel stable` never does.
#
# Downloads the release binary for this OS/arch from GitHub Releases,
# verifies it against the release's SHA256SUMS, and installs it as `daytona`.
set -eu

REPO="${DAYTONA_RELEASE_REPO:-daytona/clients}"
VERSION="${DAYTONA_VERSION:-}"
CHANNEL="${DAYTONA_CHANNEL:-}"
INSTALL_DIR="${DAYTONA_INSTALL_DIR:-}"

while [ $# -gt 0 ]; do
  case "$1" in
    --version|--channel|--dir)
      [ $# -ge 2 ] || { echo "$1 requires a value" >&2; exit 2; }
      case "$1" in
        --version) VERSION="$2" ;;
        --channel) CHANNEL="$2" ;;
        --dir) INSTALL_DIR="$2" ;;
      esac
      shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
case "$CHANNEL" in
  ""|stable|rc|alpha) ;;
  *) echo "unknown channel: $CHANNEL (stable, rc or alpha)" >&2; exit 2 ;;
esac
if [ -n "$VERSION" ] && [ -n "$CHANNEL" ]; then
  echo "pass either a version or a channel, not both" >&2
  exit 2
fi

os="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "$os" in linux|darwin) ;; *) echo "unsupported OS: $os (use install.ps1 on Windows)" >&2; exit 1 ;; esac
arch="$(uname -m)"
case "$arch" in
  x86_64|amd64) arch=amd64 ;;
  aarch64|arm64) arch=arm64 ;;
  *) echo "unsupported architecture: $arch" >&2; exit 1 ;;
esac

# rank <line>: "MAJOR MINOR PATCH STAGE N TAG" for a 2.x CLI release tag, where
# STAGE orders alpha (0) < rc (1) < stable (2); nothing for any other tag (0.x
# releases, beta or other suffixes, SDK tags).
rank() {
  n='(0|[1-9][0-9]*)'
  sed -E -n \
    -e "s/^v2\\.$n\\.$n\$/2 \\1 \\2 2 0 &/p" \
    -e "s/^v2\\.$n\\.$n-rc\\.$n\$/2 \\1 \\2 1 \\3 &/p" \
    -e "s/^v2\\.$n\\.$n-alpha\\.$n\$/2 \\1 \\2 0 \\3 &/p"
}

if [ -z "$VERSION" ]; then
  # Every published release of the repository (it also carries the 0.x line):
  # "TAG DRAFT PRERELEASE" per release, read from the fields of each release
  # object in whatever order they come (nested objects and strings skipped).
  tags=""
  page=1
  while :; do
    body="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases?per_page=100&page=${page}")" \
      || { echo "could not list the releases of ${REPO}; pass --version X.Y.Z" >&2; exit 1; }
    # Split on quotes: lines alternate between text outside strings (scanned
    # for structure) and string contents (kept only for keys and tag_name).
    found="$(printf '%s' "$body" | tr -d '\r\n' | tr '"' '\n' | awk '
      instr {
        esc = match($0, /\\+$/) ? RLENGTH % 2 : 0
        if (keep) str = str $0 (esc ? "\"" : "")
        if (esc) next
        instr = 0
        if (depth == 2 && !value) key = str
        else if (depth == 2 && key == "tag_name") { tag = str; hastag = 1 }
        if (depth == 2) value = 0
        next
      }
      {
        n = length($0)
        for (i = 1; i <= n; i++) {
          c = substr($0, i, 1)
          # A number or null value (the last field of a release object can
          # be one) is never closed by a token here: reset it on entry.
          if (c == "{" || c == "[") { if (depth == 1) { tag = ""; hastag = 0; draft = ""; pre = "" } depth++; value = 0; continue }
          if (c == "}" || c == "]") {
            depth--
            # One line per release with all three fields; a tag that is empty
            # or holds blanks stays one field (and is no release version).
            if (depth == 1 && hastag && draft != "" && pre != "") { gsub(/[ \t]/, "_", tag); print (tag == "" ? "-" : tag), draft, pre }
            if (depth == 2) value = 0
            continue
          }
          if (depth != 2) continue
          if (c == ":") { value = 1; continue }
          if (c == ",") { value = 0; continue }
          if (value && (c == "t" || c == "f")) {
            if (key == "draft") draft = (c == "t" ? "true" : "false")
            if (key == "prerelease") pre = (c == "t" ? "true" : "false")
            value = 0
          }
        }
        instr = 1; str = ""; keep = depth == 2 && (!value || key == "tag_name")
      }')"
    # Only an empty page ends the list; a page whose releases could not be
    # read is an error, never a shorter list.
    if [ -z "$found" ]; then
      [ "$(printf '%s' "$body" | tr -d ' \t\r\n')" = "[]" ] && break
      echo "could not read the release list of ${REPO} (page ${page}); pass --version X.Y.Z" >&2
      exit 1
    fi
    # At most 50 pages (5,000 releases) are read; a longer list is an error,
    # never a truncated one.
    if [ "$page" -gt 50 ]; then
      echo "${REPO} has more than 5000 releases, more than this installer reads; pass --version X.Y.Z" >&2
      exit 1
    fi
    tags="${tags}${found}
"
    page=$((page + 1))
  done
  # Drafts never count, and a stable-looking tag GitHub marks as a pre-release
  # is not stable.
  tags="$(printf '%s' "$tags" | awk '$2 == "false" && !($3 == "true" && $1 !~ /-/) { print $1 }')"
  ranked="$(printf '%s\n' "$tags" | rank | sort -n -k1,1 -k2,2 -k3,3 -k4,4 -k5,5)"
  case "$CHANNEL" in
    ""|stable) min=2 ;;
    rc) min=1 ;;
    alpha) min=0 ;;
  esac
  VERSION="$(printf '%s\n' "$ranked" | awk -v min="$min" 'NF == 6 && $4 >= min { tag = $6 } END { print tag }')"
  if [ -z "$VERSION" ] && [ -z "$CHANNEL" ]; then
    VERSION="$(printf '%s\n' "$ranked" | awk 'NF == 6 { tag = $6 } END { print tag }')"
    [ -z "$VERSION" ] || echo "note: no stable 2.x release yet, so installing the highest pre-release, ${VERSION}; from 2.0.0 on the default is the highest stable release (--channel alpha or rc opts into pre-releases)" >&2
  fi
  [ -n "$VERSION" ] || { echo "found no ${CHANNEL:-stable} 2.x CLI release of ${REPO}; pass --channel alpha or --version X.Y.Z" >&2; exit 1; }
fi
VERSION="${VERSION#v}"

base="https://github.com/${REPO}/releases/download/v${VERSION}"
asset="daytona-${os}-${arch}"
tmp="$(mktemp -d)"
staged=""
trap 'rm -rf "$tmp"; [ -z "$staged" ] || rm -f "$staged"' EXIT

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
if [ -d "$INSTALL_DIR/daytona" ]; then
  echo "$INSTALL_DIR/daytona is a directory; remove it or pass --dir" >&2
  exit 1
fi
# Check that the new binary runs before it replaces the installed one. It is
# staged beside the target: the install directory allows execution where /tmp
# may not, and the final mv is a rename on the same filesystem.
staged="$INSTALL_DIR/.daytona.new.$$"
mv "$tmp/$asset" "$staged"
if ! "$staged" version; then
  echo "the downloaded daytona ${VERSION} failed to run; $INSTALL_DIR/daytona was not changed" >&2
  exit 1
fi
mv -f "$staged" "$INSTALL_DIR/daytona"
staged=""
echo "installed $INSTALL_DIR/daytona"
case ":$PATH:" in *":$INSTALL_DIR:"*) ;; *) echo "note: $INSTALL_DIR is not on your PATH" ;; esac
