#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Table test for release-channel.sh: what sdk_publish.yaml derives from a
# release version's channel. Run: bash scripts/release-channel_test.sh
set -euo pipefail
cd "$(dirname "$0")"

fail() { echo "FAIL: $*" >&2; exit 1; }

# accept <input> <channel> <prerelease> <npm tag> <formula> <event> <workflow>
accept() {
  local out want
  out=$(./release-channel.sh "$1") || fail "$1: exit $? (want 0)"
  want=$(printf 'channel=%s\nprerelease=%s\nnpm_dist_tag=%s\nhomebrew_formula=%s\nhomebrew_event=%s\nhomebrew_workflow=%s' "${@:2}")
  [ "$out" = "$want" ] || fail "$1: got
$out
want
$want"
}

# reject <input> — exit 1, nothing on stdout, the input named on stderr.
reject() {
  local out err rc=0
  out=$(./release-channel.sh "$1" 2>"$TMP_ERR") || rc=$?
  err=$(cat "$TMP_ERR")
  [ "$rc" -eq 1 ] || fail "'$1': exit $rc (want 1); stdout: $out"
  [ -z "$out" ] || fail "'$1': expected no stdout, got: $out"
  grep -qF -- "'$1' has no release channel" <<<"$err" || fail "'$1': stderr does not name the input: $err"
}

TMP_ERR=$(mktemp); trap 'rm -f "$TMP_ERR"' EXIT

accept v2.0.0-alpha.8 alpha true alpha daytona-alpha update-version-alpha brew_publish_alpha.yaml
accept 2.1.0-alpha.0  alpha true alpha daytona-alpha update-version-alpha brew_publish_alpha.yaml
accept v2.0.0-rc.2    rc true next daytona-rc update-version-rc brew_publish_rc.yaml
accept v2.0.0         stable false latest daytona update-version brew_publish.yaml
accept 2.10.3         stable false latest daytona update-version brew_publish.yaml

reject v2.0.0-beta.1     # no formula follows betas
reject ""
reject 2.0.0-alpha       # no counter
reject 2.01.0            # leading zero
reject 2.0.0-rc.01
reject 2.0.0+build.1
reject 2.0.0a3           # PEP 440 spelling is output, never input
reject vv2.0.0

rc=0; ./release-channel.sh >/dev/null 2>&1 || rc=$?
[ "$rc" -eq 2 ] || fail "no argument: exit $rc (want 2)"

# Every version sdk-version.sh derives for a channel classifies the same way
# here, so the two helpers never disagree about a version the gate accepts.
for t in v2.0.0-alpha.3 v2.0.0-rc.4 v2.0.0; do
  cli=$(./sdk-version.sh "$t" | sed -n 's/^cli=//p')
  ./release-channel.sh "$cli" >/dev/null || fail "$t: sdk-version's cli '$cli' has no channel"
done

echo "release-channel.sh: all table tests passed"
