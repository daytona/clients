#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

echo "→ poetry-lock-retry"

# During publish, the api clients were released to PyPI moments ago and the
# PyPI CDN can serve stale version listings for a few minutes. `--no-cache`
# forces poetry to re-read the live index instead of a stale cached copy, and
# the retry loop rides out inconsistent CDN edges (same approach as
# add-api-clients.sh).

# --- tunables ---------------------------------------------------------------
deadline_seconds=300 # give up after 5 minutes of wall-clock time
max_delay=30         # cap for the exponential backoff
delay=1              # first backoff interval
# ---------------------------------------------------------------------------

start_time=$(date +%s)
last_error=""

while true; do
  if output=$(poetry lock --regenerate --no-cache 2>&1); then
    echo "Successfully regenerated lock file"
    exit 0
  fi
  last_error="$output"

  remaining=$(( deadline_seconds - ($(date +%s) - start_time) ))
  if [ "$remaining" -le 0 ]; then
    echo "Failed to regenerate lock file within ${deadline_seconds}s" >&2
    echo "Last error output:" >&2
    echo "$last_error" >&2
    exit 1
  fi

  nap="$delay"
  [ "$nap" -gt "$remaining" ] && nap="$remaining"
  echo "poetry lock failed; retrying in ${nap}s (deadline in ${remaining}s)"
  sleep "$nap"

  delay=$(( delay * 2 ))
  [ "$delay" -gt "$max_delay" ] && delay="$max_delay"
done
