#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

echo "→ add-api-clients"

if [ -z "${PYPI_PKG_VERSION:-}" ]; then
  echo "PYPI_PKG_VERSION not set; skipping add-api-clients"
  exit 0
fi

version="$PYPI_PKG_VERSION"
echo "Adding API clients at version $version"

# --- tunables ---------------------------------------------------------------
deadline_seconds=300 # give up after 5 minutes of wall-clock time
max_delay=30         # cap for the exponential backoff
delay=1              # first backoff interval
# ---------------------------------------------------------------------------

start_time=$(date +%s)
last_error=""

while true; do
  # --no-cache forces poetry to re-read the live PyPI index instead of a stale
  # cached copy. That stale simple-index cache is the actual cause of
  # "Could not find a matching version" even after the release is on PyPI.
  if output=$(poetry add --no-cache \
    "daytona_api_client@$version" \
    "daytona_api_client_async@$version" \
    "daytona_toolbox_api_client@$version" \
    "daytona_toolbox_api_client_async@$version" \
    "daytona_analytics_api_client@$version" \
    "daytona_analytics_api_client_async@$version" 2>&1); then
    echo "Successfully added API clients"
    exit 0
  fi
  last_error="$output"

  remaining=$(( deadline_seconds - ($(date +%s) - start_time) ))
  if [ "$remaining" -le 0 ]; then
    echo "Failed to add API clients within ${deadline_seconds}s" >&2
    echo "Last error output:" >&2
    echo "$last_error" >&2
    exit 1
  fi

  nap="$delay"
  [ "$nap" -gt "$remaining" ] && nap="$remaining"
  echo "poetry add failed; retrying in ${nap}s (deadline in ${remaining}s)"
  sleep "$nap"

  delay=$(( delay * 2 ))
  [ "$delay" -gt "$max_delay" ] && delay="$max_delay"
done
