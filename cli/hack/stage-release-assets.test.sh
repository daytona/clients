#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: AGPL-3.0
#
# Dry run of the release asset step: stage fake binaries under both CLI names,
# check the asset names, labels and SHA256SUMS, then install the daytona-v0
# assets with install-v0.sh against a stubbed GitHub (curl on PATH) and, when
# pwsh is on PATH, with install-v0.ps1 (web calls shadowed by functions).
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
[ -e "${work}/daytona/release/install-v0.ps1" ] && fail "install-v0.ps1 must not ship with daytona-named assets"
(cd "${work}/daytona/release" && sha256sum --quiet -c SHA256SUMS) || fail "daytona SHA256SUMS does not verify"
[ "$(wc -l <"${work}/daytona/release/SHA256SUMS")" -eq 6 ] || fail "daytona SHA256SUMS must list the 6 binaries"

# Renamed: daytona-v0 assets, the install scripts, checksums over all of them.
uploads="$(stage daytona-v0)"
echo "$uploads" | grep -qx "${work}/daytona-v0/release/daytona-v0-linux-amd64#daytona-v0-cli-v0.300.0-linux-amd64" \
  || fail "missing renamed linux-amd64 upload: $uploads"
echo "$uploads" | grep -qx "${work}/daytona-v0/release/daytona-v0-windows-arm64.exe#daytona-v0-cli-v0.300.0-windows-arm64.exe" \
  || fail "missing renamed windows-arm64 upload: $uploads"
echo "$uploads" | grep -qx "${work}/daytona-v0/release/install-v0.sh" || fail "missing install-v0.sh upload"
echo "$uploads" | grep -qx "${work}/daytona-v0/release/install-v0.ps1" || fail "missing install-v0.ps1 upload"
echo "$uploads" | grep -E '/daytona-(linux|darwin|windows)-' && fail "daytona-v0 run uploads daytona-named assets"
[ "$(echo "$uploads" | wc -l)" -eq 9 ] || fail "want 9 daytona-v0 uploads, got: $uploads"
(cd "${work}/daytona-v0/release" && sha256sum --quiet -c SHA256SUMS) || fail "daytona-v0 SHA256SUMS does not verify"
grep -q ' install-v0.sh$' "${work}/daytona-v0/release/SHA256SUMS" || fail "SHA256SUMS must cover install-v0.sh"
grep -q ' install-v0.ps1$' "${work}/daytona-v0/release/SHA256SUMS" || fail "SHA256SUMS must cover install-v0.ps1"

# Renamed with the daytona alias (until a stable 2.x release exists): the
# daytona-v0 uploads plus the same bytes as daytona-<os>-<arch>, all in SHA256SUMS.
dual="${work}/dual"
uploads="$(CLI_BINARY_NAME=daytona-v0 CLI_ALIAS_NAME=daytona CLI_DIST_DIR="${work}/daytona-v0/dist" CLI_RELEASE_DIR="$dual" \
  bash "${script_dir}/stage-release-assets.sh" v0.300.0)" || fail "staging with CLI_ALIAS_NAME=daytona failed"
[ "$(echo "$uploads" | wc -l)" -eq 15 ] || fail "want 15 uploads with the daytona alias, got: $uploads"
for p in "${platforms[@]}"; do
  ext=""
  [[ "$p" == windows-* ]] && ext=.exe
  echo "$uploads" | grep -qx "${dual}/daytona-${p}${ext}#daytona-cli-v0.300.0-${p}${ext}" \
    || fail "missing alias upload daytona-${p}${ext}: $uploads"
  echo "$uploads" | grep -qx "${dual}/daytona-v0-${p}${ext}#daytona-v0-cli-v0.300.0-${p}${ext}" \
    || fail "missing daytona-v0-${p}${ext} upload next to the alias: $uploads"
  cmp -s "${dual}/daytona-${p}${ext}" "${dual}/daytona-v0-${p}${ext}" || fail "daytona-${p}${ext} differs from daytona-v0-${p}${ext}"
  grep -q " daytona-${p}${ext}\$" "${dual}/SHA256SUMS" || fail "SHA256SUMS must cover daytona-${p}${ext}"
done
echo "$uploads" | grep -qx "${dual}/install-v0.sh" || fail "missing install-v0.sh upload with the daytona alias"
echo "$uploads" | grep -qx "${dual}/install-v0.ps1" || fail "missing install-v0.ps1 upload with the daytona alias"
[ "$(wc -l <"${dual}/SHA256SUMS")" -eq 14 ] || fail "SHA256SUMS must list 12 binaries and the two install scripts"
(cd "$dual" && sha256sum --quiet -c SHA256SUMS) || fail "SHA256SUMS with the daytona alias does not verify"

# The alias only applies to daytona-v0 assets, and only as daytona.
if CLI_ALIAS_NAME=daytona CLI_DIST_DIR="${work}/daytona/dist" CLI_RELEASE_DIR="${work}/bad-alias" \
  bash "${script_dir}/stage-release-assets.sh" v0.300.0 >/dev/null 2>&1; then
  fail "CLI_ALIAS_NAME accepted for daytona-named assets"
fi
if CLI_BINARY_NAME=daytona-v0 CLI_ALIAS_NAME=daytona-v2 CLI_DIST_DIR="${work}/daytona-v0/dist" CLI_RELEASE_DIR="${work}/bad-alias" \
  bash "${script_dir}/stage-release-assets.sh" v0.300.0 >/dev/null 2>&1; then
  fail "unknown CLI_ALIAS_NAME accepted"
fi

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

# install-v0.ps1: Windows PowerShell 5.1 parses web responses with the Internet
# Explorer engine unless -UseBasicParsing is passed; pwsh ignores the switch, so
# only this check catches a missing one.
calls="$(grep -vE '^\s*#' "${cli_dir}/install-v0.ps1" | grep -E 'Invoke-(RestMethod|WebRequest) ')"
[ "$(echo "$calls" | wc -l)" -eq 3 ] || fail "install-v0.ps1: want 3 web calls (release list, binary, SHA256SUMS), got: $calls"
echo "$calls" | grep -v -- '-UseBasicParsing' && fail "install-v0.ps1: web call without -UseBasicParsing"
grep -q '/releases/latest' "${cli_dir}/install-v0.ps1" && fail "install-v0.ps1 must rank releases itself, not read releases/latest"

# install-v0.ps1 under pwsh the way `irm | iex` runs it, against the same
# release listing, with the web calls shadowed. The fake .exe is a shell script.
# run_ps1 <install dir> [DAYTONA_VERSION]
run_ps1() {
  DAYTONA_INSTALL_DIR="$1" DAYTONA_VERSION="${2:-}" INSTALL_PS1="${cli_dir}/install-v0.ps1" \
    MOCK_ASSETS="${work}/daytona-v0/release" TEMP="$work" PROCESSOR_ARCHITECTURE=AMD64 PROCESSOR_ARCHITEW6432= \
    pwsh -NoProfile -NonInteractive -Command '
$ErrorActionPreference = "Continue"
function Invoke-RestMethod {
  param([Parameter(Position = 0)] $Uri, [switch] $UseBasicParsing)
  if ($Uri -notmatch "/repos/daytona/clients/releases\?per_page=100&page=(\d+)$") { throw "unexpected url $Uri" }
  if ($Matches[1] -ne "1") { return ,@() }
  $rel = { param($tag, $draft, $pre) [pscustomobject]@{ tag_name = $tag; draft = $draft; prerelease = $pre; assets = @([pscustomobject]@{ name = "a" }) } }
  return ,@((& $rel v2.0.0 $false $false), (& $rel v0.302.0 $true $false), (& $rel v0.301.0 $false $true), (& $rel v0.300.0 $false $false),
    (& $rel v2.1.0-alpha.1 $false $true), (& $rel v0.299.1 $false $false), (& $rel v0.301.0-rc.1 $false $true), (& $rel v0.30.0 $false $false))
}
function Invoke-WebRequest {
  param([Parameter(Position = 0)] $Uri, [switch] $UseBasicParsing, $OutFile)
  if ($Uri -notlike "https://github.com/daytona/clients/releases/download/v0.300.0/*") { throw "404 $Uri" }
  $src = Join-Path $env:MOCK_ASSETS (Split-Path $Uri -Leaf)
  if (-not (Test-Path $src)) { throw "404 $Uri" }
  Copy-Item $src $OutFile
  if ($OutFile -like "*.exe") { & chmod +x $OutFile }
}
try { Get-Content -Raw $env:INSTALL_PS1 | Invoke-Expression } catch { Write-Host "INSTALL_FAILED: $_" }
Write-Host "CALLER_EAP=$ErrorActionPreference"' 2>&1
}
if command -v pwsh >/dev/null 2>&1; then
  ps_dir="${work}/ps-bin"
  out="$(run_ps1 "$ps_dir")"
  echo "$out" | grep -q "INSTALL_FAILED" && fail "install-v0.ps1 failed: $out"
  echo "$out" | grep -q "downloading daytona-v0 0.300.0 (windows/amd64)" || fail "install-v0.ps1 did not pick v0.300.0: $out"
  echo "$out" | grep -q "daytona-v0 v0.300.0 (windows-amd64)" || fail "install-v0.ps1 did not run the new binary: $out"
  echo "$out" | grep -q "CALLER_EAP=Continue" || fail "install-v0.ps1 changed the caller's ErrorActionPreference: $out"
  cmp -s "${ps_dir}/daytona-v0.exe" "${work}/daytona-v0/release/daytona-v0-windows-amd64.exe" \
    || fail "install-v0.ps1 did not install daytona-v0.exe: $out"
  [ -e "${ps_dir}/daytona.exe" ] && fail "install-v0.ps1 must not install a daytona.exe"
  [ "$(find "$ps_dir" -name '.daytona-v0.new.*' | wc -l)" -eq 0 ] || fail "install-v0.ps1 left a staged binary"

  out="$(run_ps1 "${work}/ps-bin2" 2.0.0)"
  echo "$out" | grep -q "installs the 0.x CLI only" || fail "install-v0.ps1 did not refuse a 2.x version: $out"

  out="$(run_ps1 "${work}/ps-bin3" v0.231.0)"
  echo "$out" | grep -q "has no daytona-v0-windows-amd64.exe asset" || fail "install-v0.ps1 did not explain a missing asset: $out"

  # A tampered binary is refused and the installed one is kept.
  cp "${work}/daytona-v0/release/daytona-v0-windows-amd64.exe" "${work}/win-orig.exe"
  echo tampered >>"${work}/daytona-v0/release/daytona-v0-windows-amd64.exe"
  out="$(run_ps1 "$ps_dir")"
  echo "$out" | grep -q "checksum mismatch for daytona-v0-windows-amd64.exe" || fail "install-v0.ps1 did not refuse a tampered binary: $out"
  cmp -s "${ps_dir}/daytona-v0.exe" "${work}/win-orig.exe" || fail "install-v0.ps1 replaced daytona-v0.exe with a tampered binary"
  cp "${work}/win-orig.exe" "${work}/daytona-v0/release/daytona-v0-windows-amd64.exe"
else
  echo "SKIP: pwsh not on PATH, install-v0.ps1 not run"
fi

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
