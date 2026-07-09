#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

echo "→ build-sdk"

if [ -n "${PYPI_PKG_VERSION:-}" ] || [ -n "${DEFAULT_PACKAGE_VERSION:-}" ]; then
  VER="${PYPI_PKG_VERSION:-$DEFAULT_PACKAGE_VERSION}"
  poetry version "$VER"
else
  echo "Using version from pyproject.toml"
fi

# Build the canonical "daytona" distribution.
poetry build

# Build the deprecated "daytona_sdk" alias from the same source. Snapshot
# pyproject.toml and restore it (and the renamed package dir) on any exit, so a
# failed alias build never leaves the working tree mutated.
cp pyproject.toml pyproject.toml.orig
restore() {
  mv -f pyproject.toml.orig pyproject.toml 2>/dev/null || true
  if [ -d src/daytona_sdk ] && [ ! -e src/daytona ]; then
    mv src/daytona_sdk src/daytona || true
  fi
}
trap restore EXIT

mv src/daytona src/daytona_sdk
sed -i \
  -e 's/^name = "[^"]*"/name = "daytona_sdk"/' \
  -e 's|^description = .*|description = "Deprecated: please migrate to the '\''daytona'\'' package. This alias is being phased out."|' \
  -e 's|^readme = "README.md"|readme = "DEPRECATED.md"|' \
  -e 's|^    "Development Status :: 3 - Alpha",|    "Development Status :: 7 - Inactive",|' \
  pyproject.toml
poetry build
