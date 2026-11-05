#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Tests for the generator-jar integrity gate in sdk-python/gen-contract.sh: a jar
# whose sha256 differs from the pinned one is never cached and never run.
# Runs offline: curl and npm are stubbed. Needs node (the script reads
# gen/openapitools.json with it).
#
#   bash sdk-python/gen/gen-contract_test.sh
set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
script="$here/../gen-contract.sh"

fail=0
pass() { echo "ok   - $1"; }
bad() { echo "FAIL - $1" >&2; fail=1; }

# sandbox [VERSION]: a scratch copy of the script and its config, with stub
# curl (writes a corrupt "jar") and npm (records that it ran) first on PATH.
sandbox() {
  local dir version="${1:-}"
  dir="$(mktemp -d)"
  mkdir -p "$dir/gen" "$dir/bin"
  cp "$script" "$dir/gen-contract.sh"
  cp "$here/openapitools.json" "$dir/gen/openapitools.json"
  if [ -n "$version" ]; then
    node -e '
      const fs = require("fs"); const p = process.argv[1];
      const c = JSON.parse(fs.readFileSync(p, "utf8"));
      c["generator-cli"].version = process.argv[2];
      fs.writeFileSync(p, JSON.stringify(c));
    ' "$dir/gen/openapitools.json" "$version"
  fi
  cat >"$dir/bin/curl" <<'SH'
#!/usr/bin/env bash
out=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; shift; fi
  shift
done
printf 'not the generator jar\n' >"$out"
SH
  cat >"$dir/bin/npm" <<'SH'
#!/usr/bin/env bash
touch "$(dirname "$0")/../npm-ran"
exit 0
SH
  chmod +x "$dir/bin/curl" "$dir/bin/npm"
  echo "$dir"
}

run_in() {
  local dir="$1"
  PATH="$dir/bin:$PATH" bash "$dir/gen-contract.sh" >"$dir/out" 2>&1
}

jar_of() {
  local dir="$1" version
  version="$(node -p "require('$dir/gen/openapitools.json')['generator-cli'].version")"
  echo "$dir/gen/.generator/$version.jar"
}

# 1. A corrupt download fails the run before the jar is cached or used.
dir="$(sandbox)"
if run_in "$dir"; then
  bad "corrupt download: the run must fail"
elif ! grep -q "checksum mismatch" "$dir/out"; then
  bad "corrupt download: expected a checksum mismatch, got: $(cat "$dir/out")"
elif [ -e "$(jar_of "$dir")" ] || [ -e "$(jar_of "$dir").tmp" ]; then
  bad "corrupt download: the jar must not be cached"
elif [ -e "$dir/npm-ran" ]; then
  bad "corrupt download: generation must not start"
else
  pass "corrupt download fails before caching"
fi
rm -rf "$dir"

# 2. A corrupt cached jar fails the run too.
dir="$(sandbox)"
jar="$(jar_of "$dir")"
mkdir -p "$(dirname "$jar")"
printf 'tampered\n' >"$jar"
if run_in "$dir"; then
  bad "corrupt cache: the run must fail"
elif ! grep -q "checksum mismatch" "$dir/out"; then
  bad "corrupt cache: expected a checksum mismatch, got: $(cat "$dir/out")"
elif [ -e "$dir/npm-ran" ]; then
  bad "corrupt cache: generation must not start"
else
  pass "corrupt cached jar fails"
fi
rm -rf "$dir"

# 3. A generator version without a pinned digest is refused outright.
dir="$(sandbox 0.0.0-unpinned)"
if run_in "$dir"; then
  bad "unpinned version: the run must fail"
elif ! grep -q "no pinned sha256" "$dir/out"; then
  bad "unpinned version: expected a missing-pin error, got: $(cat "$dir/out")"
elif [ -e "$(jar_of "$dir").tmp" ] || [ -e "$(jar_of "$dir")" ]; then
  bad "unpinned version: nothing may be downloaded"
else
  pass "unpinned version is refused"
fi
rm -rf "$dir"

exit "$fail"
