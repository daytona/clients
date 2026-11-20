#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Run the real installer function without cloning or changing system files.
set -euo pipefail
cd "$(dirname "$0")/../../.."
root=$PWD
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
awk '/^install_go\(\)/ { active=1 } active { print } active && /^}/ { exit }' \
  "$root/scripts/ax-evals/setup/_common.sh" > "$tmp/install.sh"

# A mismatch must fail safely instead of running any installation commands.
refuse_system_change() { echo "unexpected system change: $*" >&2; return 1; }
SUDO=refuse_system_change
go() {
  case "$*" in
    "env GOVERSION") printf 'go%s\n' "$installed" ;;
    version) printf 'go version go%s linux/amd64\n' "$installed" ;;
    *) echo "unexpected go command: $*" >&2; return 1 ;;
  esac
}
t() { printf '%s\n' "$*"; }
source "$tmp/install.sh"

check() {
  local label=$1 module=$2
  installed=$3
  # When the requested version is already installed, no reinstall is allowed.
  install_go "$module"
  # Then the chosen toolchain also feeds setup/go.sh's generated module.
  [ "$GO_VERSION" = "$installed" ] || {
    echo "FAIL: $label chose $GO_VERSION, expected $installed" >&2
    exit 1
  }
  echo "PASS: $label"
}

# Given a minimum Go version different from the preferred toolchain.
printf 'module example.com/eval\n\ngo 1.26.0\n\ntoolchain go1.26.10\n' > "$tmp/go.mod"
check "toolchain after go" "$tmp/go.mod" 1.26.10
printf 'module example.com/eval\n\ntoolchain go1.26.10\n\ngo 1.26.0\n' > "$tmp/go.mod"
check "toolchain before go" "$tmp/go.mod" 1.26.10
printf 'module example.com/eval\n\ngo 1.25.3\n' > "$tmp/go.mod"
check "go-only fallback" "$tmp/go.mod" 1.25.3
printf 'module example.com/eval\n\ngo 1.25.3\n\ntoolchain default\n' > "$tmp/go.mod"
check "default toolchain fallback" "$tmp/go.mod" 1.25.3
