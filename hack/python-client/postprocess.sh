#!/usr/bin/env bash
set -euo pipefail

# This script normalizes generated Python client metadata after OpenAPI generation.
# Usage: postprocess.sh <projectRoot>

if [ $# -lt 1 ]; then
  echo "Usage: $0 <projectRoot>" >&2
  exit 1
fi

PROJECT_ROOT="$1"

# Set license in pyproject.toml to Apache-2.0
sed -i 's/^license = ".*"/license = "Apache-2.0"/' "$PROJECT_ROOT/pyproject.toml"

# Generated clients must not advertise Python 3.9 support: security patches of
# urllib3/aiohttp ship only for >=3.10, and 3.9 has been EOL since October 2025.
# pyproject.toml is covered by the local pyproject.mustache template override;
# setup.py still comes from the upstream template, so sync it here.
sed -i 's/^requires-python = ">=3.9"/requires-python = ">=3.10"/' "$PROJECT_ROOT/pyproject.toml"
sed -i 's/^PYTHON_REQUIRES = ">= 3.9"/PYTHON_REQUIRES = ">= 3.10"/' "$PROJECT_ROOT/setup.py"

# Ensure urllib3 lower bound is pinned to version 2.7.0 in pyproject.toml, setup.py, and requirements.txt.
# 2.7.0 carries the fixes for GHSA-mf9v-mfxr-j63j and GHSA-qccp-gfcp-xxvc (and,
# historically, the >=2.1.0 PoolKey 'key_ca_cert_data' compatibility floor).
# pyproject.toml already gets 2.7.0 from the template override; this keeps
# setup.py and requirements.txt (upstream templates) consistent.
sed -i -E 's/(urllib3[^0-9\n]*)([0-9]+\.[0-9]+\.[0-9]+)/\12.7.0/g' \
  "$PROJECT_ROOT/pyproject.toml" \
  "$PROJECT_ROOT/setup.py" \
  "$PROJECT_ROOT/requirements.txt"

# Replace all aliases with serialization_aliases in the models directory so that type checking works.
pkg_root=$(find "$PROJECT_ROOT" -mindepth 1 -maxdepth 2 -type f -name "py.typed" -printf '%h\n' | head -n 1)
MODELS_DIR="$pkg_root/models"
find "$MODELS_DIR" -type f -name "*.py" | while read -r f; do
  sed -i'' -E '/Field\(/ s/alias="([^"]+)"/serialization_alias="\1"/g' "$f"
done

# Remove hardcoded 300s HTTP timeout fallback from async REST clients
# so that timeout=None means no timeout, matching TypeScript SDK behavior.
# Server-side already handles stale connections.
sed -i 's/timeout = _request_timeout or 5 \* 60/timeout = _request_timeout/' "$pkg_root/rest.py"

# Set dynamic User-Agent with package version
CLIENT_NAME=$(basename "$PROJECT_ROOT")
sed -i '/^from.*\.configuration import Configuration$/a from . import __version__ as _pkg_version' "$pkg_root/api_client.py"
sed -i "s|self\.user_agent = '[^']*'|self.user_agent = f'${CLIENT_NAME}/{_pkg_version}'|" "$pkg_root/api_client.py"

echo "Postprocessed Python client at $PROJECT_ROOT"
