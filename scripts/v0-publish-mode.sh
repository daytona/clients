#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Decide whether a publish of the source tree at ROOT (default: the repo root
# this script lives in) bundles the v0 namespace. Prints `bundle` or `skip`;
# exits non-zero, with a GitHub `::error::`, when the tree is inconsistent.
#
#   - v0 namespace code present (sdk-typescript/src/v0-unbundled.ts or
#     sdk-python/src/daytona/_client.py) and a pin (scripts/v0-sdk-version,
#     or a DAYTONA_V0_TAG testing override)            -> bundle
#   - neither the namespace code nor a pin (a release from before the
#     namespaces, e.g. v2.0.0-alpha.1)                  -> skip (with a notice)
#   - namespace code but no pin                         -> error: publishing
#     would ship packages whose `d.v0` can never work
#   - a pin but no namespace code                       -> error
#   - the namespace code in only one of the two SDKs    -> error
#
# The publish gate runs main's copy of this script against source_ref's tree
# (sdk_publish.yaml); sdk-python/tests/test_root_client.py covers each case.
set -euo pipefail

# --check-override: refuse a DAYTONA_V0_TAG (the `v0_tag` input) whenever the
# run publishes to PyPI or npm (PUBLISH_PYPI / PUBLISH_NPM = true), unless
# ALLOW_UNPINNED_V0=true acknowledges shipping an unpinned v0 namespace.
if [ "${1:-}" = --check-override ]; then
  # Refuse CR/LF before echoing the value into a workflow command line.
  case ${DAYTONA_V0_TAG:-} in *$'\n'* | *$'\r'*) echo "v0-publish-mode: v0_tag must be a single line" >&2; exit 1 ;; esac
  # The same pin resolve-v0-tag.sh reads: DAYTONA_V0_PIN_FILE (the publish gate
  # points it at source_ref's pin), else the pin beside this script.
  pin_file=${DAYTONA_V0_PIN_FILE:-"$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/v0-sdk-version"}
  pinned_tag=$(sed -n '1p' "$pin_file" 2>/dev/null | sed -e 's/\r$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
  # Naming the pinned tag itself is not an override (the pinned commit check still runs).
  if [ -n "${DAYTONA_V0_TAG:-}" ] && [ "$DAYTONA_V0_TAG" != "$pinned_tag" ] && { [ "${PUBLISH_PYPI:-}" = true ] || [ "${PUBLISH_NPM:-}" = true ]; }; then
    if [ "${ALLOW_UNPINNED_V0:-}" != true ]; then
      echo "::error::v0_tag=$DAYTONA_V0_TAG overrides the pinned v0 namespace on a run that publishes to PyPI/npm; set allow_unpinned_v0=true to ship it knowingly, or clear v0_tag" >&2
      exit 1
    fi
    echo "::warning::publishing with v0_tag=$DAYTONA_V0_TAG (allow_unpinned_v0): the v0 namespace is UNVERIFIED unless it is the pinned tag" >&2
  fi
  exit 0
fi

# The files that mark "this tree has the v0 namespace code", per SDK. If they
# move, the decision below would silently read a current tree as pre-namespace:
# `--check-markers` (an sdk-ci job) fails when either is missing from this tree.
PY_MARKER=sdk-python/src/daytona/_client.py
TS_MARKER=sdk-typescript/src/v0-unbundled.ts

if [ "${1:-}" = --check-markers ]; then
  root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
  for marker in "$PY_MARKER" "$TS_MARKER"; do
    if [ ! -f "$root/$marker" ]; then
      echo "::error::v0-publish-mode.sh marker $marker no longer exists; update PY_MARKER / TS_MARKER" >&2
      exit 1
    fi
  done
  echo "v0-publish-mode markers present"
  exit 0
fi

root=${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}

# Per SDK: Python's namespace code is daytona/_client.py, TypeScript's is
# src/v0-unbundled.ts. Both SDKs ship the namespace together, so one having
# it while the other lacks it is an inconsistent tree.
py_code=false
ts_code=false
[ -f "$root/$PY_MARKER" ] && py_code=true
[ -f "$root/$TS_MARKER" ] && ts_code=true
if [ "$py_code" != "$ts_code" ]; then
  echo "::error::source_ref has the v0 namespace code in only one SDK (python: $py_code, typescript: $ts_code); refusing an inconsistent tree" >&2
  exit 1
fi
has_code=$py_code
has_pin=false
if [ -f "$root/scripts/v0-sdk-version" ] || [ -n "${DAYTONA_V0_TAG:-}" ]; then
  has_pin=true
fi

if $has_code && $has_pin; then
  echo bundle
elif ! $has_code && ! $has_pin; then
  echo "::notice::source_ref predates the v0 namespace (no namespace code, no scripts/v0-sdk-version): publishing without a v0 bundle" >&2
  echo skip
elif $has_code; then
  echo "::error::source_ref has the v0 namespace code but no scripts/v0-sdk-version pin: refusing to publish packages whose Daytona().v0 cannot work" >&2
  exit 1
else
  echo "::error::source_ref has scripts/v0-sdk-version but no v0 namespace code (sdk-typescript/src/v0-unbundled.ts, sdk-python/src/daytona/_client.py): refusing an inconsistent tree" >&2
  exit 1
fi
