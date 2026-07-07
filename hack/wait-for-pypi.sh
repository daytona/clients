#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Wait until an exact package version is visible on PyPI.
#
# Freshly published releases are not immediately installable: PyPI's index is
# served through a CDN and clients cache index metadata locally, so a version
# can take a while to become resolvable. This polls PyPI (bypassing pip's HTTP
# cache) with exponential backoff until the version shows up or a deadline hits.
#
# Usage: wait-for-pypi.sh <package> <version>

set -euo pipefail

pkg="${1:?usage: wait-for-pypi.sh <package> <version>}"
version="${2:?usage: wait-for-pypi.sh <package> <version>}"

# --- tunables ---------------------------------------------------------------
deadline_seconds=300 # give up after 5 minutes of wall-clock time
max_delay=30         # cap for the exponential backoff
delay=1              # first backoff interval
# ---------------------------------------------------------------------------

start_time=$(date +%s)
# Escape dots so e.g. 0.194.0 doesn't loosely match 0x194x0.
version_re=${version//./\\.}

echo "→ waiting for ${pkg}==${version} on PyPI"

while true; do
  # --no-cache-dir: bypass pip's stale HTTP metadata cache.
  # --pre: pip hides pre-releases (e.g. 0.194.0a1) by default; without this the
  #        loop never matches an alpha/beta version and times out.
  if pip index versions "$pkg" --pre --no-cache-dir 2>/dev/null \
    | grep -Eq "(^|[[:space:],(])${version_re}([[:space:],)]|$)"; then
    echo "${pkg}==${version} is available on PyPI"
    exit 0
  fi

  remaining=$(( deadline_seconds - ($(date +%s) - start_time) ))
  if [ "$remaining" -le 0 ]; then
    echo "Timed out after ${deadline_seconds}s waiting for ${pkg}==${version} on PyPI" >&2
    exit 1
  fi

  nap="$delay"
  [ "$nap" -gt "$remaining" ] && nap="$remaining"
  echo "  not visible yet; retrying in ${nap}s (deadline in ${remaining}s)"
  sleep "$nap"

  delay=$(( delay * 2 ))
  [ "$delay" -gt "$max_delay" ] && delay="$max_delay"
done
