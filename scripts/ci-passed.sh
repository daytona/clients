#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# The `CI passed` gate (.github/workflows/ci-passed.yaml): waits for every
# other `pull_request` workflow run of this repository on a commit and fails
# unless each of them succeeded. Path-filtered workflows that did not start on
# the commit have no run and are not waited for; a run that concluded
# `skipped` or `neutral` counts as passed. Only the latest run of each
# workflow counts, and its conclusion is that of its latest attempt, so a
# re-run replaces an earlier failure.
#
# Usage: scripts/ci-passed.sh <owner/repo> <commit-sha>
# Needs `gh` and `jq`; GH_TOKEN with `actions: read`.
#
#   CI_PASSED_IGNORE    workflow files not to wait for, space-separated paths
#                       (e.g. ".github/workflows/ci-passed.yaml")
#   CI_PASSED_TIMEOUT   seconds to wait for runs to finish (default 3600)
#   CI_PASSED_INTERVAL  seconds between polls (default 20)
#   CI_PASSED_ONCE      1: evaluate once and exit 2 if runs are still pending
#                       instead of waiting (for local checks against old commits)

set -euo pipefail

repo=${1:?usage: ci-passed.sh <owner/repo> <commit-sha>}
sha=${2:?usage: ci-passed.sh <owner/repo> <commit-sha>}
if ! [[ $sha =~ ^[0-9a-f]{40}$ ]]; then
  echo "::error::not a full commit SHA: $sha" >&2
  exit 1
fi
ignore=${CI_PASSED_IGNORE:-}
timeout=${CI_PASSED_TIMEOUT:-3600}
interval=${CI_PASSED_INTERVAL:-20}
once=${CI_PASSED_ONCE:-0}

# One line per counted run: <state> <workflow> (<status|conclusion>) <url>,
# state being pending, passed or failed.
evaluate() {
  gh api --paginate "repos/$repo/actions/runs?head_sha=$sha&event=pull_request&per_page=100" \
    --jq '.workflow_runs[]' |
    jq -rs --arg ignore "$ignore" '
      ($ignore | split(" ") | map(select(. != ""))) as $ignored
      | map(select(.path as $p | $ignored | index($p) | not))
      | group_by(.workflow_id) | map(max_by(.id))
      | sort_by(.name)[]
      | (if .status != "completed" then "pending"
         elif (.conclusion | IN("success", "skipped", "neutral")) then "passed"
         else "failed" end) as $state
      | "\($state) \(.name) (\(.conclusion // .status), attempt \(.run_attempt)) \(.html_url)"'
}

deadline=$((SECONDS + timeout))
previous=
while :; do
  result=$(evaluate)
  if [ "$result" != "$previous" ]; then
    printf '%s\n%s\n\n' "--- $(date -u +%H:%M:%S)" "${result:-no other workflow runs on this commit}"
    previous=$result
  fi

  if grep -q '^failed ' <<<"$result"; then
    grep '^failed ' <<<"$result" | while read -r _ line; do echo "::error::$line"; done
    echo "Re-run the failed workflow; this check re-runs itself when the re-run completes." >&2
    exit 1
  fi
  if ! grep -q '^pending ' <<<"$result"; then
    echo "All workflow runs on $sha passed."
    exit 0
  fi
  if [ "$once" = 1 ]; then
    echo "Workflow runs still pending." >&2
    exit 2
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    grep '^pending ' <<<"$result" | while read -r _ line; do echo "::error::still running after ${timeout}s: $line"; done
    exit 1
  fi
  sleep "$interval"
done
