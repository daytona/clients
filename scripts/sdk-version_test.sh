#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Table test for sdk-version.sh: the version → registry-version derivation
# that sdk_publish.yaml runs in its gate. Run: bash scripts/sdk-version_test.sh
set -euo pipefail
cd "$(dirname "$0")"

fail() { echo "FAIL: $*" >&2; exit 1; }

# accept <input> <version> <pypi> — the full output must be exactly the five
# key=value lines, and --pep440 must agree with the pypi line.
accept() {
  local input="$1" version="$2" pypi="$3" out want
  out=$(./sdk-version.sh "$input") || fail "$input: exit $? (want 0)"
  want=$(printf 'version=%s\nnpm=%s\ncli=%s\ncli_tag=v%s\npypi=%s' "$version" "$version" "$version" "$version" "$pypi")
  [ "$out" = "$want" ] || fail "$input: got
$out
want
$want"
  out=$(./sdk-version.sh --pep440 "$input") || fail "--pep440 $input: exit $?"
  [ "$out" = "$pypi" ] || fail "--pep440 $input: got '$out', want '$pypi'"
}

# reject <input> — exit 1, nothing on stdout, the input named on stderr.
reject() {
  local input="$1" out err rc=0
  out=$(./sdk-version.sh "$input" 2>"$TMP_ERR") || rc=$?
  err=$(cat "$TMP_ERR")
  [ "$rc" -eq 1 ] || fail "'$input': exit $rc (want 1); stdout: $out"
  [ -z "$out" ] || fail "'$input': expected no stdout, got: $out"
  grep -qF -- "'${input}' is not a release version" <<<"$err" || fail "'$input': stderr does not name the input: $err"
  rc=0; out=$(./sdk-version.sh --pep440 "$input" 2>/dev/null) || rc=$?
  [ "$rc" -eq 1 ] && [ -z "$out" ] || fail "--pep440 '$input': exit $rc, stdout '$out' (want 1, empty)"
}

TMP_ERR=$(mktemp); TMP_MOD=$(mktemp); trap 'rm -f "$TMP_ERR" "$TMP_MOD"' EXIT

# ── The derivation table ───────────────────────────────────────────────────
accept 2.0.0-alpha.3   2.0.0-alpha.3   2.0.0a3
accept 2.0.0-rc.1      2.0.0-rc.1      2.0.0rc1
accept 2.0.0           2.0.0           2.0.0
# beta, the `v` prefix the tag carries, multi-digit and zero components
accept v2.0.0-beta.2   2.0.0-beta.2    2.0.0b2
accept v2.0.0-alpha.1  2.0.0-alpha.1   2.0.0a1
accept v10.20.30-alpha.40 10.20.30-alpha.40 10.20.30a40
accept 0.0.0-alpha.0   0.0.0-alpha.0   0.0.0a0

# ── Rejections ────────────────────────────────────────────────────────────────
reject ""                       # empty
reject main                     # a branch name
reject 2.0                      # not three components
reject 2.0.0.1                  # four components
reject 2.01.0                   # leading zero (npm/PEP 440 would canonicalize it)
reject 2.0.0-alpha.01           # leading zero in the pre-release counter
reject 2.0.0-alpha              # pre-release without a counter
reject 2.0.0-alpha.3.1          # extra pre-release identifier
reject 2.0.0-ALPHA.3            # wrong case
reject 2.0.0-dev.3              # unknown pre-release kind
reject 2.0.0-pre.1              # unknown pre-release kind
reject 2.0.0+build.5            # build metadata
reject 2.0.0-alpha.3+sha.abc    # build metadata on a pre-release
reject 2.0.0a3                  # PEP 440 spelling is output, never input
reject package-v2.0.0          # non-version prefix
reject vv2.0.0                  # double prefix
reject " 2.0.0"                 # whitespace
reject 0.0.0-dev                # the unstamped default is not a release

# Go has its own, source-validated major path and subdirectory tag.
for version in v2.1.0 2.2.0-alpha.1 v2.1.0-rc.2 v3.0.0 v10.20.30; do
  bare=${version#v} major=${version#v}; major=${major%%.*}
  printf 'module go.daytona.com/sdk/v%s\n\ngo 1.26.10\n' "$major" > "$TMP_MOD"
  out=$(./sdk-version.sh --go "$version" "$TMP_MOD") || fail "Go $version"
  want=$(printf 'go=v%s\ngo_tag=sdk-go/v%s\ngo_module=go.daytona.com/sdk/v%s' "$bare" "$bare" "$major")
  [ "$out" = "$want" ] || fail "Go $version: got $out, want $want"
done
for declaration in \
  'module go.daytona.com/sdk/v2 // Go SDK module' \
  $'\tmodule\tgo.daytona.com/sdk/v2\t// release module' \
  'module go.daytona.com/sdk/v2 //'; do
  printf '%s\n' "$declaration" > "$TMP_MOD"
  out=$(./sdk-version.sh --go v2.1.0 "$TMP_MOD") || fail "Go trailing comment: $declaration"
  want=$'go=v2.1.0\ngo_tag=sdk-go/v2.1.0\ngo_module=go.daytona.com/sdk/v2'
  [ "$out" = "$want" ] || fail "Go trailing comment: got $out, want $want"
done
for declaration in \
  'module go.daytona.com/sdk/v2' \
  'module go.daytona.com/sdk/v3' \
  'module github.com/daytona/clients/sdk-go' \
  'module go.daytona.com/sdk' \
  'module go.daytona.com/sdk/v02' \
  'module go.daytona.com/sdk/v2/extra' \
  'module "go.daytona.com/sdk/v2"' \
  'module go.daytona.com/sdk/v2 extra' \
  'module go.daytona.com/sdk/v2 extra // not a valid declaration' \
  'module go.daytona.com/sdk/v3 // wrong major' \
  'module go.daytona.com/sdk/v2//not-a-comment' \
  $'module go.daytona.com/sdk/v2\nmodule go.daytona.com/sdk/v2' \
  ''; do
  printf '%s\n' "$declaration" > "$TMP_MOD"
  for version in v0.233.0 v1.0.0 v2.1.0 v2.2.0-alpha.1; do
    [ "$declaration" != 'module go.daytona.com/sdk/v2' ] || [[ "$version" != v2.* ]] || continue
    rc=0; out=$(./sdk-version.sh --go "$version" "$TMP_MOD" 2>"$TMP_ERR") || rc=$?
    if [ "$rc" -ne 1 ] || [ -n "$out" ] || ! grep -q '::error::' "$TMP_ERR"; then
      fail "Go must reject $version with '$declaration': rc=$rc stdout=$out"
    fi
  done
done
rc=0; out=$(./sdk-version.sh --go v2.1.0 "$TMP_MOD.missing" 2>"$TMP_ERR") || rc=$?
if [ "$rc" -ne 1 ] || [ -n "$out" ] || ! grep -q '::error::' "$TMP_ERR"; then fail 'Go missing module'; fi
grep -qF 'sdk-go/go.mod' "$TMP_ERR" && grep -qF 'untick publish_go' "$TMP_ERR" \
  || fail "Go missing module must tell the operator to untick publish_go: $(cat "$TMP_ERR")"

# ── Usage errors ──────────────────────────────────────────────────────────────
rc=0; ./sdk-version.sh >/dev/null 2>&1 || rc=$?
[ "$rc" -eq 2 ] || fail "no argument: exit $rc (want 2)"
rc=0; ./sdk-version.sh 2.0.0 2.0.1 >/dev/null 2>&1 || rc=$?
[ "$rc" -eq 2 ] || fail "two arguments: exit $rc (want 2)"
rc=0; ./sdk-version.sh --bogus 2.0.0 >/dev/null 2>&1 || rc=$?
[ "$rc" -eq 2 ] || fail "unknown flag: exit $rc (want 2)"

# ── The gate's regexes accept exactly the alpha, rc and stable derivations ────
# sdk_publish.yaml validates the DERIVED strings with these two patterns (the
# same text as in its "Derive registry versions" step); pin here that alpha, rc
# and stable tags yield strings they accept and that a beta yields strings they
# reject, since no Homebrew formula follows betas.
num='(0|[1-9][0-9]*)'
pypi_re="^${num}\.${num}\.${num}((a|rc)${num})?$"
npm_re="^${num}\.${num}\.${num}(-(alpha|rc)\.${num})?$"
grep -qF -- "\"\$pypi\" =~ ^\${num}\.\${num}\.\${num}((a|rc)\${num})?\$" ../.github/workflows/sdk_publish.yaml \
  || fail "sdk_publish.yaml's PyPI gate regex differs from this test's"
grep -qF -- "\"\$npm\" =~ ^\${num}\.\${num}\.\${num}(-(alpha|rc)\.\${num})?\$" ../.github/workflows/sdk_publish.yaml \
  || fail "sdk_publish.yaml's npm gate regex differs from this test's"
grep -qF -- "\"\$cli\" =~ ^\${num}\.\${num}\.\${num}(-(alpha|rc)\.\${num})?\$" ../.github/workflows/sdk_publish.yaml \
  || fail "sdk_publish.yaml's CLI gate regex differs from this test's"
npm="" cli="" # Assigned by the trusted helper's eval below.
for t in v2.0.0-alpha.3 v2.0.0-rc.2 v2.0.0 v2.1.10; do
  eval "$(./sdk-version.sh "$t")"
  [[ "$pypi" =~ $pypi_re ]] || fail "$t: pypi '$pypi' must pass the gate"
  [[ "$npm" =~ $npm_re ]] || fail "$t: npm '$npm' must pass the gate"
  [[ "$cli" =~ $npm_re ]] || fail "$t: cli '$cli' must pass the gate"
done
eval "$(./sdk-version.sh v2.0.0-beta.1)"
! [[ "$pypi" =~ $pypi_re ]] || fail "beta pypi '$pypi' must NOT pass the gate"
! [[ "$npm" =~ $npm_re ]] || fail "beta npm '$npm' must NOT pass the gate"

echo "sdk-version.sh: all table tests passed"
