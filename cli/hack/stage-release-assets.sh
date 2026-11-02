#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: AGPL-3.0
#
# Stage the CLI release assets that release.yaml uploads to the GitHub release.
#
# Usage: stage-release-assets.sh <version>
#
# Reads the binaries hack/build.sh wrote to dist/cli (CLI_DIST_DIR), copies them
# to dist/cli-release (CLI_RELEASE_DIR) together with a SHA256SUMS file and, for
# the daytona-v0 name, the install-v0.sh script. Prints one `gh release upload`
# argument per line: `path#label` for binaries, the bare path for the rest.
#
# CLI_BINARY_NAME (daytona or daytona-v0, default daytona) selects the asset
# names: <name>-<os>-<arch>[.exe].

set -euo pipefail

version="${1:?usage: stage-release-assets.sh <version>}"
name="${CLI_BINARY_NAME:-daytona}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cli_dir="$(cd "${script_dir}/.." && pwd)"
repo_root="$(cd "${cli_dir}/.." && pwd)"
dist_dir="${CLI_DIST_DIR:-${repo_root}/dist/cli}"
release_dir="${CLI_RELEASE_DIR:-${repo_root}/dist/cli-release}"

case "$name" in
  daytona | daytona-v0) ;;
  *)
    echo "CLI_BINARY_NAME must be daytona or daytona-v0 (got '$name')" >&2
    exit 1
    ;;
esac

platforms=(linux-amd64 linux-arm64 darwin-amd64 darwin-arm64 windows-amd64 windows-arm64)

rm -rf "$release_dir"
mkdir -p "$release_dir"

assets=()
uploads=()
for platform in "${platforms[@]}"; do
  asset="${name}-${platform}"
  [[ "$platform" == windows-* ]] && asset="${asset}.exe"
  if [ ! -f "${dist_dir}/${asset}" ]; then
    echo "missing CLI binary ${dist_dir}/${asset}; build it with CLI_BINARY_NAME=${name} hack/build.sh" >&2
    exit 1
  fi
  cp "${dist_dir}/${asset}" "${release_dir}/${asset}"
  assets+=("$asset")
  label="${name}-cli-${version}-${platform}"
  [[ "$platform" == windows-* ]] && label="${label}.exe"
  uploads+=("${release_dir}/${asset}#${label}")
done

# The 0.x install script resolves the newest v0 release and installs daytona-v0,
# so it only ships with releases whose assets carry that name.
if [ "$name" = daytona-v0 ]; then
  cp "${cli_dir}/install-v0.sh" "${release_dir}/install-v0.sh"
  assets+=(install-v0.sh)
  uploads+=("${release_dir}/install-v0.sh")
fi

(cd "$release_dir" && sha256sum "${assets[@]}" >SHA256SUMS)
uploads+=("${release_dir}/SHA256SUMS")

printf '%s\n' "${uploads[@]}"
