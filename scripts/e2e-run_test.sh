#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Exercise the real dispatcher without installing SDKs or making live calls.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
dispatch=$(sed -n '/^rc=0/,$p' "$root/e2e/run.sh")
default=$(grep '^WHICH=' "$root/e2e/run.sh")

run_case() {
  local mode=$1 expected=$2 expected_rc=${3:-0} output status=0
  # Given suite functions that expose invocation order and a selectable failure.
  # When the script's own default and dispatcher select the suites.
  output=$(DEFAULT="$default" DISPATCH="$dispatch" PY_RC="${4:-0}" bash -c '
    set -euo pipefail
    run_py() { echo py; return "$PY_RC"; }
    run_ts() { echo ts; }
    run_go() { echo go; }
    eval "$DEFAULT"
    eval "$DISPATCH"
  ' -- ${mode:+"$mode"} 2>&1) || status=$?
  # Then every selected suite runs, in order, preserving a failing exit code.
  [[ "$status" -eq "$expected_rc" && "$output" == "$expected" ]] || {
    printf 'FAIL: mode=%s status=%s output=%s\n' "$mode" "$status" "$output" >&2
    exit 1
  }
}

run_case "" $'py\nts'
run_case both $'py\nts'
run_case py py
run_case ts ts
run_case go go
run_case all $'py\nts\ngo'
run_case all $'py\nts\ngo' 7 7
echo "PASS: default and existing modes preserved; all runs every SDK after failures"
