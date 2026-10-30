#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: AGPL-3.0
#
# Dry run of the release asset step: stage fake binaries under both CLI names,
# check the asset names, labels and SHA256SUMS, then install the daytona-v0
# assets with install-v0.sh against a stubbed GitHub (curl on PATH).
#
# Usage: bash cli/hack/stage-release-assets.test.sh

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cli_dir="$(cd "${script_dir}/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

failures=0
fail() {
  echo "FAIL: $*"
  failures=$((failures + 1))
}

platforms=(linux-amd64 linux-arm64 darwin-amd64 darwin-arm64 windows-amd64 windows-arm64)

make_binaries() {
  local name="$1" dir="$2" platform asset
  mkdir -p "$dir"
  for platform in "${platforms[@]}"; do
    asset="${name}-${platform}"
    [[ "$platform" == windows-* ]] && asset="${asset}.exe"
    printf '#!/bin/sh\necho "%s v0.300.0 (%s)"\n' "$name" "$platform" >"${dir}/${asset}"
    chmod +x "${dir}/${asset}"
  done
}

# stage <name>: name `daytona` leaves CLI_BINARY_NAME unset to cover the default.
stage() {
  local name="$1"
  make_binaries "$name" "${work}/${name}/dist"
  (
    unset CLI_BINARY_NAME
    [ "$name" = daytona ] || export CLI_BINARY_NAME="$name"
    CLI_DIST_DIR="${work}/${name}/dist" CLI_RELEASE_DIR="${work}/${name}/release" \
      bash "${script_dir}/stage-release-assets.sh" v0.300.0
  )
}

# Default name: today's asset names and labels, plus checksums.
uploads="$(stage daytona)"
expected="$(
  for p in "${platforms[@]}"; do
    ext=""
    [[ "$p" == windows-* ]] && ext=.exe
    echo "${work}/daytona/release/daytona-${p}${ext}#daytona-cli-v0.300.0-${p}${ext}"
  done
  echo "${work}/daytona/release/SHA256SUMS"
)"
[ "$uploads" = "$expected" ] || fail "daytona uploads differ:"$'\n'"$uploads"$'\n'"want:"$'\n'"$expected"
[ -e "${work}/daytona/release/install-v0.sh" ] && fail "install-v0.sh must not ship with daytona-named assets"
(cd "${work}/daytona/release" && sha256sum --quiet -c SHA256SUMS) || fail "daytona SHA256SUMS does not verify"
[ "$(wc -l <"${work}/daytona/release/SHA256SUMS")" -eq 6 ] || fail "daytona SHA256SUMS must list the 6 binaries"

# Renamed: daytona-v0 assets, install-v0.sh, checksums over all of them.
uploads="$(stage daytona-v0)"
echo "$uploads" | grep -qx "${work}/daytona-v0/release/daytona-v0-linux-amd64#daytona-v0-cli-v0.300.0-linux-amd64" \
  || fail "missing renamed linux-amd64 upload: $uploads"
echo "$uploads" | grep -qx "${work}/daytona-v0/release/daytona-v0-windows-arm64.exe#daytona-v0-cli-v0.300.0-windows-arm64.exe" \
  || fail "missing renamed windows-arm64 upload: $uploads"
echo "$uploads" | grep -qx "${work}/daytona-v0/release/install-v0.sh" || fail "missing install-v0.sh upload"
echo "$uploads" | grep -E '/daytona-(linux|darwin|windows)-' && fail "daytona-v0 run uploads daytona-named assets"
[ "$(echo "$uploads" | wc -l)" -eq 8 ] || fail "want 8 daytona-v0 uploads, got: $uploads"
(cd "${work}/daytona-v0/release" && sha256sum --quiet -c SHA256SUMS) || fail "daytona-v0 SHA256SUMS does not verify"
grep -q ' install-v0.sh$' "${work}/daytona-v0/release/SHA256SUMS" || fail "SHA256SUMS must cover install-v0.sh"

# A name other than the two lines is refused.
if CLI_BINARY_NAME=daytona-v2 CLI_DIST_DIR="${work}/daytona/dist" CLI_RELEASE_DIR="${work}/bad" \
  bash "${script_dir}/stage-release-assets.sh" v0.300.0 >/dev/null 2>&1; then
  fail "unknown CLI_BINARY_NAME accepted"
fi

# Missing binary is refused.
rm "${work}/daytona-v0/dist/daytona-v0-darwin-arm64"
if CLI_BINARY_NAME=daytona-v0 CLI_DIST_DIR="${work}/daytona-v0/dist" CLI_RELEASE_DIR="${work}/missing" \
  bash "${script_dir}/stage-release-assets.sh" v0.300.0 >/dev/null 2>&1; then
  fail "missing binary accepted"
fi

# install-v0.sh against a stubbed GitHub: the release listing mixes 2.x,
# prereleases (one with a bare v0.X.Y tag), a draft and older 0.x releases, in
# GitHub's shape (nested author and asset objects); downloads come from the
# staged directory.
stub="${work}/stub"
mkdir -p "$stub"
cat >"${stub}/curl" <<EOF
#!/usr/bin/env bash
out=""; url=""
while [ \$# -gt 0 ]; do
  case "\$1" in
    -o) out="\$2"; shift 2 ;;
    -*) shift ;;
    *) url="\$1"; shift ;;
  esac
done
case "\$url" in
  https://api.github.com/repos/daytona/clients/releases\?per_page=100)
    rel() { printf '{"id": 1, "author": {"login": "bot", "type": "Bot"}, "tag_name": "%s", "name": "%s", "draft": %s, "prerelease": %s, "assets": [{"name": "a", "uploader": {"login": "bot"}}]}' "\$1" "\$1" "\$2" "\$3"; }
    body="[\$(rel v2.0.0 false false),\$(rel v0.302.0 true false),\$(rel v0.301.0 false true),\$(rel v0.300.0 false false),\$(rel v2.1.0-alpha.1 false true),\$(rel v0.299.1 false false),\$(rel v0.301.0-rc.1 false true),\$(rel v0.30.0 false false)]"
    if [ -n "\$out" ]; then echo "\$body" >"\$out"; else echo "\$body"; fi ;;
  https://github.com/daytona/clients/releases/download/v0.300.0/*)
    cp "${work}/daytona-v0/release/\${url##*/}" "\$out" 2>/dev/null || exit 22 ;;
  *) echo "unexpected url \$url" >&2; exit 22 ;;
esac
EOF
chmod +x "${stub}/curl"
install_dir="${work}/bin"
out="$(PATH="${stub}:$PATH" DAYTONA_INSTALL_DIR="$install_dir" sh "${cli_dir}/install-v0.sh" 2>&1)" \
  || fail "install-v0.sh failed: $out"
[ -x "${install_dir}/daytona-v0" ] || fail "install-v0.sh did not install daytona-v0: $out"
[ -e "${install_dir}/daytona" ] && fail "install-v0.sh must not install a daytona binary"
echo "$out" | grep -q "downloading daytona-v0 0.300.0" || fail "install-v0.sh did not pick v0.300.0: $out"

# A tampered binary is refused.
echo tampered >>"${work}/daytona-v0/release/daytona-v0-linux-amd64"
echo tampered >>"${work}/daytona-v0/release/daytona-v0-linux-arm64"
echo tampered >>"${work}/daytona-v0/release/daytona-v0-darwin-amd64"
echo tampered >>"${work}/daytona-v0/release/daytona-v0-darwin-arm64"
if out="$(PATH="${stub}:$PATH" DAYTONA_INSTALL_DIR="${work}/bin2" sh "${cli_dir}/install-v0.sh" 2>&1)" \
  || ! echo "$out" | grep -q "checksum mismatch"; then
  fail "install-v0.sh did not refuse a binary that does not match SHA256SUMS: $out"
fi

# Only 0.x versions are accepted.
if out="$(PATH="${stub}:$PATH" DAYTONA_INSTALL_DIR="${work}/bin3" sh "${cli_dir}/install-v0.sh" --version 2.0.0 2>&1)" \
  || ! echo "$out" | grep -q "installs the 0.x CLI only"; then
  fail "install-v0.sh did not refuse a 2.x version: $out"
fi

if [ "$failures" -ne 0 ]; then
  echo "$failures failure(s)"
  exit 1
fi
echo "stage-release-assets: all checks passed"
