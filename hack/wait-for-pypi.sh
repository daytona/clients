#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Wait until an exact package version is visible on PyPI.
#
# WHY TWO PROBES: freshly published releases propagate through PyPI's CDN
# (Fastly). The simple index page (/simple/<pkg>/, what pip reads) existed
# BEFORE the release, so a stale cached copy that lacks the new version can be
# served for a long time if a CDN purge is lost or delayed. Retrying against it
# is futile: every retry can get the same stale object (`--no-cache-dir` only
# bypasses pip's local cache, not the CDN). The version-specific JSON endpoint
# (/pypi/<pkg>/<version>/json) did NOT exist before the release, so it cannot
# have a stale pre-release copy; a per-request cache-busting query param also
# prevents a prematurely cached 404 from sticking. We therefore probe the JSON
# endpoint first (authoritative for "the release exists") and the pip simple
# index second (authoritative for "installers can see it"). EITHER passing
# counts as visible: the JSON endpoint proves the release is live at origin,
# and the downstream consumer (add-api-clients) has its own retry loop for
# resolver-level staleness.
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

json_probe() {
  # Version-specific URL: no pre-release stale copy can exist. The cache-buster
  # gives every poll a distinct cache key so a cached 404 can't stick either.
  local code
  code=$(curl -fsSL -o /dev/null -w '%{http_code}' --max-time 30 \
    "https://pypi.org/pypi/${pkg}/${version}/json?nocache=$(date +%s%N)" 2>/dev/null) || true
  [ "$code" = "200" ]
}

pip_probe() {
  # --no-cache-dir: bypass pip's stale HTTP metadata cache.
  # --pre: pip hides pre-releases (e.g. 0.194.0a1) by default; without this the
  #        loop never matches an alpha/beta version and times out.
  pip index versions "$pkg" --pre --no-cache-dir 2>/dev/null \
    | grep -Eq "(^|[[:space:],(])${version_re}([[:space:],)]|$)"
}

while true; do
  if json_probe; then
    echo "${pkg}==${version} is live on PyPI (JSON API)"
    exit 0
  fi
  if pip_probe; then
    echo "${pkg}==${version} is visible on PyPI (simple index)"
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
