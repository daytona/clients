#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
# Read-only diff heuristics. Warnings are review prompts, not automatic defects.
set -euo pipefail

if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  cat <<'HELP'
Usage: preflight.sh <merge-base-ref> <head-ref> [checkout-dir]
Requires Git and jq. Reports SDK parity/generation, contract, CLI, docs,
changelog, deleted-file, and artifact hints for a committed diff.
Exit 0 means collection succeeded, not that warnings are resolved.
HELP
  exit 0
fi
[[ $# -ge 2 && $# -le 3 ]] || { echo 'Expected base, head, and optional checkout directory' >&2; exit 2; }
for tool in git jq; do
  command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 2; }
done
wt="${3:-.}"
g() { git -C "$wt" "$@"; }
base="$(g rev-parse --verify --end-of-options "$1^{commit}")"
head="$(g rev-parse --verify --end-of-options "$2^{commit}")"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
# Keep paths NUL-delimited until JSON conversion, including unusual filenames.
g diff --name-only --no-renames -z "$base" "$head" > "$tmp/changed.z"
g diff --name-only --no-renames --diff-filter=A -z "$base" "$head" > "$tmp/added.z"
g diff --name-only --no-renames --diff-filter=D -z "$base" "$head" > "$tmp/deleted.z"
for kind in changed added deleted; do
  jq -Rs 'split("\u0000") | map(select(length > 0))' "$tmp/$kind.z" > "$tmp/$kind.json"
done
printf '# Preflight — %s..%s\n\n' "${base:0:8}" "${head:0:8}"
jq -nr --slurpfile changed "$tmp/changed.json" --slurpfile added "$tmp/added.json" \
  --slurpfile deleted "$tmp/deleted.json" '
  $changed[0] as $c | $added[0] as $a | $deleted[0] as $d |
  def touched($re): any($c[]; test($re));
  def paths: map("- " + tojson) | if length == 0 then ["- none"] else . end | .[];
  def hint($test; $text): if $test then "- " + $text else empty end;
  "## Added artifact candidates", "",
  ($a | map(select(test("(^|/)(dist|build|node_modules|\\.venv|__pycache__)/|\\.(pyc|log|tgz|orig|rej)$|(^|/)\\.coverage($|\\.)"))) | paths),
  "", "## Mirror and generation review", "",
  hint(touched("^sdk-python/src/") and (touched("^sdk-typescript/src/") | not);
    "Python source changed without TypeScript source: justify parity and check both test suites."),
  hint(touched("^sdk-typescript/src/") and (touched("^sdk-python/src/") | not);
    "TypeScript source changed without Python source: justify parity and check both test suites."),
  hint(touched("^sdk-python/src/daytona/v2/|^sdk-python/gen/");
    "Check authored async methods against generated _sync/; run sync surface tests and generation in a disposable copy."),
  hint(touched("(^contract/|/codes\\.(py|ts)$)");
    "Check both SDK code catalogs, per-code extras, route drift tests, and committed contract artifacts."),
  hint(touched("^contract/|^sdk-python/gen/|^sdk-typescript/scripts/gen-contract|^cli/hack/gen|^scripts/gen-contract-types|^sdk-python/src/daytona/v2/_gen/|^sdk-typescript/src/v2/gen/|^cli/gen/");
    "Check generated wire types against contract/ with scripts/gen-contract-types.sh --check; distinguish handwritten alias files."),
  hint(touched("^cli/");
    "Compare CLI and SDK shared defaults, config precedence, wire behavior, and error handling; inspect CLI-only semantics separately."),
  "- Always inspect README/code and fragment/diff pairs; file-level changes alone cannot establish parity.",
  "", "## Changelog and version review", "",
  hint(touched("^(sdk-python/src/|sdk-typescript/src/|cli/)") and
    (any($a[]; test("^changelog\\.d/.+\\.md$") and . != "changelog.d/README.md") | not);
    "Source changed without a newly added changelog.d/ fragment: add coverage for user-visible changes or explain no visible effect."),
  hint(touched("^CHANGELOG\\.md$");
    "CHANGELOG.md changed directly: verify this is release assembly, not unreleased feature notes."),
  hint(touched("^sdk-python/pyproject\\.toml$|^sdk-typescript/package\\.json$|^sdk-typescript/src/version\\.ts$");
    "Inspect version fields: tracked versions are publish placeholders, not per-PR bumps."),
  "", "## Documentation and fragments touched", "",
  ($c | map(select(test("(^|/)README\\.md$|^docs/(adr/|[^/]+\\.md$)|^changelog\\.d/|^AGENTS\\.md$|DEVELOPMENT\\.md$"))) | paths),
  "", "## Deleted files", "", ($d | paths)'
printf '\n## Removed guard, cleanup, and test candidates\n\n'
g diff -U0 "$base" "$head" -- '*.go' '*.ts' '*.py' > "$tmp/code.diff"
awk '/^-/ && !/^---/ && tolower($0) ~ /timeout|cancel|close|finally|defer|redact|assert|test|retry|cursor|guard/ {
  if (++count <= 40) print "    " $0
}' "$tmp/code.diff"
printf '\nHeuristics only: inspect every deletion and all mirror pairs manually.\n'
