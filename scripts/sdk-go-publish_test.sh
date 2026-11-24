#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Execute the workflow's actual shell with fake GitHub/proxy commands. Nothing
# contacts a registry or creates a real ref. Run: bash scripts/sdk-go-publish_test.sh
set -euo pipefail
cd "$(dirname "$0")/.."
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/bin"

fail() { echo "FAIL: $*" >&2; exit 1; }
extract() {
  awk -v name="$1" '
    index($0, "- name: " name) { step=1; next }
    step && /^        run: \|$/ { body=1; next }
    body && /^          / { print substr($0, 11); next }
    body && /^$/ { print; next }
    body { exit }
  ' .github/workflows/sdk_publish.yaml > "$2"
  [ -s "$2" ] || fail "missing workflow step: $1"
}
extract 'Refuse a version the registry already has (unless force_republish)' "$tmp/registry"
extract 'Release the Go SDK tag and wait for the module proxy' "$tmp/publish"
extract 'Every requested target was built' "$tmp/built"
extract 'Require at least one target' "$tmp/targets"
# The toolbox route drift test reads this sibling TypeScript fixture.
for event in push pull_request; do
  awk -v event="$event" '
    $0 == "  " event ":" { active=1; next }
    active && /^  [^ ]/ { exit }
    active { print }
  ' .github/workflows/sdk-go-ci.yaml | grep -qF "'sdk-typescript/tests/unit.test.ts'" \
    || fail "Go SDK CI $event paths omit sdk-typescript/tests/unit.test.ts"
done
# Tooling must run this guard when the guarded workflow itself changes.
awk '
  /^  pull_request:/ { active=1; next }
  active && /^  [^ ]/ { exit }
  active { print }
' .github/workflows/tooling.yaml | grep -qF "'.github/workflows/sdk-go-ci.yaml'" \
  || fail "Tooling pull_request paths omit sdk-go-ci.yaml"
# Model the workflow dependency: publishing runs only after registry-check.
printf 'bash %q && bash %q\n' "$tmp/registry" "$tmp/publish" > "$tmp/checked-publish"

cat > "$tmp/bin/gh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$CALLS"
case "$*" in
  *'/git/matching-refs/'*)
    [ "$TAG_STATE" != error ] || exit 1
    if [ "$TAG_STATE" != absent ]; then echo "refs/tags/$GO_TAG"; fi ;;
  *'/commits/'*)
    case "$TAG_STATE" in
      same|annotated) echo "$SOURCE_SHA" ;;
      different) echo bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb ;;
      *) exit 1 ;;
    esac ;;
  *'/git/refs -f ref=refs/tags/sdk-go/v2.1.0 -f sha='*)
    [ "$TAG_STATE" = absent ] || exit 1
    [ "${CREATE_FAIL:-false}" != true ] || exit 1 ;;
  *) echo "unexpected gh: $*" >&2; exit 1 ;;
esac
SH
cat > "$tmp/bin/curl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$CALLS"
out="" url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    https://*) url="$1"; shift ;;
    *) shift ;;
  esac
done
if [ "$url" = 'https://go.daytona.com/sdk/v2?go-get=1' ]; then
  printf '%s' "$VANITY_BODY" > "$out"
  printf '%s' "$VANITY_CODE"
  exit "$VANITY_EXIT"
fi
[[ "$url" = https://proxy.golang.org/* ]] || { echo "unexpected curl URL: $url" >&2; exit 1; }
code=$PROXY_CODE
if [ "$code" = retry ]; then
  if [ -f "$RUNNER_TEMP/retried" ]; then code=200
  else touch "$RUNNER_TEMP/retried"; code=404
  fi
fi
if [ "$out" != /dev/null ]; then printf '%s' "$PROXY_BODY" > "$out"; fi
printf '%s' "$code"
exit "${CURL_EXIT:-0}"
SH
cat > "$tmp/bin/sleep" <<'SH'
#!/usr/bin/env bash
echo sleep >> "$CALLS"
SH
chmod +x "$tmp/bin/"*
export PATH="$tmp/bin:$PATH" RUNNER_TEMP="$tmp"
export GITHUB_REPOSITORY=daytona/clients SOURCE_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
export GO_VERSION=v2.1.0 GO_TAG=sdk-go/v2.1.0 GO_MODULE=go.daytona.com/sdk/v2
export PYPI_VERSION="" NPM_VERSION="" CLI_VERSION="" UPGRADE_VERSION="" UPGRADE_NPM="" UPGRADE_PYPI="" NPM_DIST_TAG=""
export PY_BUILD=skipped NPM_BUILD=skipped CLI_BUILD=skipped UPGRADE_BUILD=skipped
export GITHUB_STEP_SUMMARY="$tmp/summary" CALLS="$tmp/calls"
export PUBLISH_PYPI=false PUBLISH_NPM=false PUBLISH_CLI=false PUBLISH_UPGRADE=false
export VANITY_CODE=404 VANITY_EXIT=0
valid_meta='<meta name="go-import" content="go.daytona.com/sdk git https://github.com/daytona/clients sdk-go">'
export VANITY_BODY="$valid_meta"

run_case() { # expected-exit script
  : > "$CALLS"; : > "$GITHUB_STEP_SUMMARY"; rm -f "$tmp/retried"
  local rc=0
  bash "$2" > "$tmp/output" 2>&1 || rc=$?
  if [ "$1" = success ]; then
    [ "$rc" -eq 0 ] || { cat "$tmp/output"; fail "$2: exit $rc"; }
  else
    [ "$rc" -ne 0 ] || fail "$2: unexpectedly succeeded"
  fi
}

# Given only Go is requested, the target and successful-build guards accept it.
export PUBLISH_GO=true GO_BUILD=success
run_case success "$tmp/targets"
run_case success "$tmp/built"
# A missing/failed/cancelled Go build must block publishing.
for GO_BUILD in skipped failure cancelled; do
  export GO_BUILD
  run_case failure "$tmp/built"
done
export PUBLISH_GO=false
run_case failure "$tmp/targets"

# A missing or wrong vanity mapping, HTTP error or failed transfer must block
# tag creation, including force_republish; plain text is not a go-import meta.
export TAG_STATE=absent PROXY_CODE=200 PROXY_BODY='{"Version":"v2.1.0"}' RESUME=false
for FORCE in false true; do
  export FORCE
  for invalid in missing wrong-prefix wrong-repo wrong-subdir text wrong-name redirect no-content http transfer; do
    VANITY_BODY="$valid_meta" VANITY_CODE=404 VANITY_EXIT=0
    case "$invalid" in
      missing) VANITY_BODY='<html><head></head></html>' ;;
      wrong-prefix) VANITY_BODY=${valid_meta/go.daytona.com\/sdk/github.com\/daytona\/clients} ;;
      wrong-repo) VANITY_BODY=${valid_meta/https:\/\/github.com\/daytona\/clients/https:\/\/github.com\/daytona\/wrong} ;;
      wrong-subdir) VANITY_BODY=${valid_meta/sdk-go/sdk-other} ;;
      text) VANITY_BODY='go.daytona.com/sdk git https://github.com/daytona/clients sdk-go' ;;
      wrong-name) VANITY_BODY=${valid_meta/go-import/description} ;;
      redirect) VANITY_CODE=302 ;;
      no-content) VANITY_CODE=204 ;;
      http) VANITY_CODE=503 ;;
      transfer) VANITY_EXIT=18 ;;
    esac
    export VANITY_BODY VANITY_CODE VANITY_EXIT
    run_case failure "$tmp/checked-publish"
    ! grep -q '/git/refs ' "$CALLS" || fail "vanity $invalid created a tag"
    ! grep -q 'proxy.golang.org' "$CALLS" || fail "vanity $invalid reached the proxy"
    for message in '::error::' 'go.daytona.com' 'scripts/go-vanity-site.sh' 'go-vanity-pages.yaml' '#688'; do
      grep -qF "$message" "$tmp/output" || fail "vanity $invalid: missing diagnostic $message"
    done
  done
done
export VANITY_BODY="$valid_meta" VANITY_EXIT=0 FORCE=false
for VANITY_CODE in 404 200; do
  export VANITY_CODE
  run_case success "$tmp/registry"
  grep -qF 'https://go.daytona.com/sdk/v2?go-get=1' "$CALLS" || fail 'vanity preflight did not run'
done
export VANITY_CODE=404
# Non-Go releases do not depend on the vanity site.
export GO_VERSION="" VANITY_EXIT=7
run_case success "$tmp/registry"
[ ! -s "$CALLS" ] || fail 'non-Go registry check contacted the vanity site'
export GO_VERSION=v2.1.0 VANITY_EXIT=0

# A proxy-listed version without its original tag is immutable, even on resume.
# Exercise both extracted workflow steps to prove no replacement tag is created.
export TAG_STATE=absent PROXY_CODE=200 PROXY_BODY=$'v2.0.0\nv2.1.0\n'
for FORCE in true false; do
  export FORCE
  run_case failure "$tmp/checked-publish"
  ! grep -q '/git/refs ' "$CALLS" || fail 'proxy-listed version recreated an absent tag'
  grep -qF 'refusing to recreate an immutable version' "$tmp/output" \
    || fail 'proxy-listed absent tag: missing immutable-version diagnostic'
done

# Registry checks fail closed, and force never accepts a different source SHA.
for FORCE in false true; do
  export FORCE
  for TAG_STATE in absent same annotated different error; do
    export TAG_STATE
    for PROXY_CODE in 200 404 410 403 500; do
      export PROXY_CODE
      for LISTED in true false; do
        # The check reads the proxy's version list (@v/list), never the
        # version's .info, so it cannot cache a "not found" before the tag exists.
        if [ "$LISTED" = true ]; then PROXY_BODY=$'v2.0.0\nv2.1.0\n'; else PROXY_BODY=$'v2.0.0\nv2.1.0-alpha.1\n'; fi
        export PROXY_BODY
        want=failure
        if [[ "$TAG_STATE" != different && "$TAG_STATE" != error ]] \
          && { [[ "$PROXY_CODE" = 404 || "$PROXY_CODE" = 410 ]] \
            || [[ "$PROXY_CODE" = 200 && ( "$LISTED" = false || ( "$FORCE" = true && "$TAG_STATE" != absent ) ) ]]; }; then want=success; fi
        run_case "$want" "$tmp/registry"
        ! grep -q '/git/refs ' "$CALLS" || fail 'registry check wrote a ref'
        ! grep -q '/@v/v2.1.0.info' "$CALLS" || fail 'registry check requested the version before its tag exists'
      done
    done
  done
done
export TAG_STATE=absent PROXY_CODE=200 CURL_EXIT=18
run_case failure "$tmp/registry"
export CURL_EXIT=0

# Publishing creates only an absent tag, or confirms a tag's peeled source SHA.
export PROXY_BODY='{"Version":"v2.1.0"}'
for RESUME in false true; do
  export RESUME
  for TAG_STATE in absent same annotated; do
    export TAG_STATE
    for PROXY_CODE in 200 retry; do
      export PROXY_CODE
      run_case success "$tmp/publish"
      grep -q 'go get go.daytona.com/sdk/v2@v2.1.0' "$GITHUB_STEP_SUMMARY" || fail 'missing install command'
      if [ "$TAG_STATE" = absent ]; then
        grep -q '/git/refs -f ref=refs/tags/sdk-go/v2.1.0 -f sha=aaaaaaaa' "$CALLS" || fail 'missing source ref creation'
      else
        ! grep -q '/git/refs ' "$CALLS" || fail 'existing tag was written'
      fi
      if [ "$PROXY_CODE" = retry ]; then grep -q '^sleep$' "$CALLS" || fail 'proxy was not retried'; fi
    done
  done
done
for TAG_STATE in different error; do
  export TAG_STATE
  run_case failure "$tmp/publish"
  ! grep -q '/git/refs ' "$CALLS" || fail 'conflicting tag was written'
  ! grep -q 'proxy.golang.org' "$CALLS" || fail 'proxy contacted for conflicting tag'
done
export TAG_STATE=absent CREATE_FAIL=true
run_case failure "$tmp/publish"
! grep -q 'proxy.golang.org' "$CALLS" || fail 'proxy contacted after failed ref creation'
export CREATE_FAIL=false PROXY_CODE=200
for body_kind in wrong malformed missing; do
  case "$body_kind" in
    wrong) PROXY_BODY='{"Version":"v2.1.1"}' ;;
    malformed) PROXY_BODY='not JSON' ;;
    missing) PROXY_BODY='{}' ;;
  esac
  export PROXY_BODY
  run_case failure "$tmp/publish"
done
# Expire only the clock in the extracted shell, not the retry implementation.
# shellcheck disable=SC2016 # Literal shell code, executed by the child bash.
sed -E 's/deadline=\$\(\( SECONDS \+ [0-9]+ \)\)/deadline=$(( SECONDS - 1 ))/' "$tmp/publish" > "$tmp/expired"
grep -qF 'deadline=$(( SECONDS - 1 ))' "$tmp/expired" || fail 'could not expire the publish deadline'
export PROXY_BODY='{"Version":"v2.1.0"}'
for PROXY_CODE in 404 410 429 500 403; do
  export PROXY_CODE
  run_case failure "$tmp/expired"
done
export PROXY_CODE=200 CURL_EXIT=18
run_case failure "$tmp/expired"
echo 'sdk-go-publish: all workflow shell tests passed'
