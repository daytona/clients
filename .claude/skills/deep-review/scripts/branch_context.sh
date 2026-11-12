#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
# Collect committed branch evidence without fetching or changing the checkout.
set -euo pipefail

if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  cat <<'HELP'
Usage: branch_context.sh [head-ref] [base-ref]
Defaults: HEAD main. Requires Git and jq. Uses local refs only; no fetch.
Prints JSON: pinned refs, overlap, worktree state, and per-commit DCO.
An empty commit range has dco_ok=false. Ref freshness is unknown.
HELP
  exit 0
fi
[[ $# -le 2 ]] || { echo 'Expected at most head and base refs' >&2; exit 2; }
for tool in git jq; do
  command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 2; }
done
head_ref="${1:-HEAD}"
base_ref="${2:-main}"
head_sha="$(git rev-parse --verify --end-of-options "$head_ref^{commit}")"
base_sha="$(git rev-parse --verify --end-of-options "$base_ref^{commit}")"
merge_base="$(git merge-base "$head_sha" "$base_sha")"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
zlist() { jq -Rs 'split("\u0000") | map(select(length > 0))'; }
git diff --name-only --no-renames -z "$merge_base" "$head_sha" | zlist > "$tmp/changed.json"
git diff --name-only --no-renames -z "$merge_base" "$base_sha" | zlist > "$tmp/base.json"
git status --porcelain=v1 --untracked-files=all > "$tmp/status.txt"
git rev-list --reverse "$merge_base..$head_sha" > "$tmp/shas.txt"
: > "$tmp/commits.jsonl"
while IFS= read -r sha; do
  author="$(git show -s --format='%an <%ae>' "$sha")"
  subject="$(git show -s --format=%s "$sha")"
  git show -s --format=%B "$sha" | git interpret-trailers --parse \
    | jq -Rs 'split("\n") | map(select(startswith("Signed-off-by: ")) | ltrimstr("Signed-off-by: "))' \
    > "$tmp/trailers.json"
  jq -n --arg sha "$sha" --arg author "$author" --arg subject "$subject" \
    --slurpfile trailers "$tmp/trailers.json" \
    '{sha: $sha, author: $author, subject: $subject,
      signed_off: ($trailers[0] | length > 0),
      signoff_matches_author: ($trailers[0] | index($author) != null)}' \
    >> "$tmp/commits.jsonl"
done < "$tmp/shas.txt"
jq -s . "$tmp/commits.jsonl" > "$tmp/commits.json"
jq -n --arg head_ref "$head_ref" --arg base_ref "$base_ref" \
  --arg head_sha "$head_sha" --arg base_sha "$base_sha" --arg merge_base "$merge_base" \
  --arg checkout_sha "$(git rev-parse HEAD)" \
  --argjson ahead "$(git rev-list --count "$base_sha..$head_sha")" \
  --argjson behind "$(git rev-list --count "$head_sha..$base_sha")" \
  --rawfile status "$tmp/status.txt" --slurpfile changed "$tmp/changed.json" \
  --slurpfile base "$tmp/base.json" --slurpfile commits "$tmp/commits.json" \
  '{head_ref: $head_ref, base_ref: $base_ref, head_sha: $head_sha,
    base_sha: $base_sha, merge_base: $merge_base, checkout_sha: $checkout_sha,
    checkout_matches_head: ($checkout_sha == $head_sha),
    ref_freshness: "unknown; local refs only", ahead_of_base: $ahead,
    behind_base: $behind, worktree_status: $status, changed_files: $changed[0],
    overlapping_files_with_base: ($changed[0] - ($changed[0] - $base[0])),
    commits: $commits[0],
    dco_ok: (($commits[0] | length > 0) and
      ($commits[0] | all(.signed_off and .signoff_matches_author)))}'
