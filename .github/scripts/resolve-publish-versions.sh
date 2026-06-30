#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Resolve and validate per-ecosystem package versions for the SDK & CLI publish
# workflow. Given a single canonical VERSION (e.g. v0.192.0 or v0.192.0-alpha.1),
# a PRERELEASE flag, the selected PACKAGES, and optional per-ecosystem overrides,
# this script:
#   - builds the dynamic publish matrix from the selected packages,
#   - converts the canonical version into each ecosystem's native form
#     (npm / PEP 440 / RubyGems / Maven / Go CLI),
#   - derives and guards the npm dist-tag,
#   - enforces the prerelease contract so that NOTHING can land on a stable /
#     default channel when PRERELEASE=true (and nothing prerelease can land on
#     a stable channel when PRERELEASE=false).
#
# Inputs (environment variables):
#   VERSION                       canonical version, must start with 'v'
#   PRERELEASE                    'true' | 'false'
#   PACKAGES                      comma-separated subset of:
#                                 typescript,analytics,python,ruby,java,cli
#   NPM_PKG_VERSION_OVERRIDE      optional, npm-native (e.g. 0.192.0-alpha.1)
#   PYPI_PKG_VERSION_OVERRIDE     optional, PEP 440 (e.g. 0.192.0a1)
#   RUBYGEMS_PKG_VERSION_OVERRIDE optional, RubyGems (e.g. 0.192.0.alpha.1)
#   MAVEN_PKG_VERSION_OVERRIDE    optional, Maven (e.g. 0.192.0-SNAPSHOT)
#   CLI_VERSION_OVERRIDE          optional, Go semver (e.g. v0.192.0)
#   NPM_TAG_OVERRIDE              optional, npm dist-tag (e.g. alpha)
#
# Outputs (written to $GITHUB_OUTPUT, or stdout when run locally):
#   matrix, npm_version, pypi_version, rubygems_version, maven_version,
#   cli_version, npm_tag, publish_cli

set -euo pipefail

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
die() {
  echo "::error::$*" >&2
  exit 1
}

note() { echo "  $*" >&2; }

# Known ecosystem tokens -> matrix metadata (name|projects|ecosystem)
declare -A TOKEN_META=(
  [typescript]="TypeScript SDK|sdk-typescript|npm"
  [analytics]="Analytics API Client|analytics-api-client|npm"
  [python]="Python SDK|sdk-python|pypi"
  [ruby]="Ruby SDK|sdk-ruby|rubygems"
  [java]="Java SDK|sdk-java|maven"
  [cli]="CLI (Homebrew)|cli|cli"
)

# Convert a canonical core+suffix (no leading 'v') into PEP 440.
to_pep440() {
  local b="$1"
  if [[ "$b" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then echo "$b"; return 0; fi
  local core="${b%%-*}" suffix="${b#*-}"
  case "$suffix" in
    alpha)   echo "${core}a0" ;;
    alpha.*) echo "${core}a${suffix#alpha.}" ;;
    beta)    echo "${core}b0" ;;
    beta.*)  echo "${core}b${suffix#beta.}" ;;
    rc)      echo "${core}rc0" ;;
    rc.*)    echo "${core}rc${suffix#rc.}" ;;
    dev)     echo "${core}.dev0" ;;
    dev.*)   echo "${core}.dev${suffix#dev.}" ;;
    *)       echo "__UNCONVERTIBLE__" ;;
  esac
}

# Convert a canonical core+suffix (no leading 'v') into a RubyGems version.
to_rubygems() {
  local b="$1"
  if [[ "$b" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then echo "$b"; return 0; fi
  local core="${b%%-*}" suffix="${b#*-}"
  echo "${core}.${suffix}"
}

# Is the given ecosystem-native version a prerelease in that ecosystem?
is_prerelease_for() {
  local eco="$1" v="$2"
  case "$eco" in
    npm)      [[ "$v" == *-* ]] ;;
    pypi)     [[ "$v" =~ (a|b|rc)[0-9]+ ]] || [[ "$v" == *.dev* ]] ;;
    rubygems) [[ "$v" =~ [a-zA-Z] ]] ;;
    maven)    [[ "$v" == *-* ]] || [[ "$v" == *SNAPSHOT* ]] ;;
    cli)      [[ "$v" == *-* ]] ;;
    *)        return 1 ;;
  esac
}

# When run outside CI, GITHUB_OUTPUT is unset; fall back to a temp file so the
# script behaves identically and stays testable locally.
OUT_FILE="${GITHUB_OUTPUT:-$(mktemp)}"
emit() { printf '%s=%s\n' "$1" "$2" >> "$OUT_FILE"; }

# ----------------------------------------------------------------------------
# Parse inputs
# ----------------------------------------------------------------------------
VERSION="${VERSION:?VERSION is required}"
PRERELEASE="${PRERELEASE:-false}"
PACKAGES="${PACKAGES:?PACKAGES is required}"

[[ "$PRERELEASE" == "true" || "$PRERELEASE" == "false" ]] \
  || die "PRERELEASE must be 'true' or 'false' (got '$PRERELEASE')"

# Canonical format: vX.Y.Z[-suffix]
[[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$ ]] \
  || die "Invalid version format: $VERSION (expected vX.Y.Z or vX.Y.Z-suffix)"

BASE="${VERSION#v}"                 # strip leading v
SUFFIX=""
[[ "$BASE" == *-* ]] && SUFFIX="${BASE#*-}"

# Prerelease-flag / version consistency on the canonical version.
if [[ "$PRERELEASE" == "true" && -z "$SUFFIX" ]]; then
  die "prerelease=true but version '$VERSION' has no prerelease suffix (e.g. ${VERSION}-alpha.1)"
fi
if [[ "$PRERELEASE" == "false" && -n "$SUFFIX" ]]; then
  die "prerelease=false but version '$VERSION' contains a prerelease suffix ('$SUFFIX'). Set prerelease=true or use a clean vX.Y.Z."
fi

# Selected token set (trim whitespace, dedupe, validate).
declare -A SELECTED=()
IFS=',' read -ra _toks <<< "$PACKAGES"
for t in "${_toks[@]}"; do
  t="$(echo "$t" | tr -d '[:space:]')"
  [[ -z "$t" ]] && continue
  [[ -n "${TOKEN_META[$t]:-}" ]] || die "Unknown package token: '$t' (allowed: ${!TOKEN_META[*]})"
  SELECTED[$t]=1
done
[[ "${#SELECTED[@]}" -gt 0 ]] || die "No packages selected"

# ----------------------------------------------------------------------------
# Resolve per-ecosystem versions + validate the prerelease contract
# ----------------------------------------------------------------------------
NPM_VERSION="${NPM_PKG_VERSION_OVERRIDE:-$BASE}"
PYPI_VERSION="${PYPI_PKG_VERSION_OVERRIDE:-$(to_pep440 "$BASE")}"
RUBYGEMS_VERSION="${RUBYGEMS_PKG_VERSION_OVERRIDE:-$(to_rubygems "$BASE")}"
MAVEN_VERSION="${MAVEN_PKG_VERSION_OVERRIDE:-$BASE}"
CLI_VERSION="${CLI_VERSION_OVERRIDE:-$VERSION}"

[[ "$PYPI_VERSION" == "__UNCONVERTIBLE__" ]] \
  && die "Cannot convert '$VERSION' to a PEP 440 version. Use a suffix of alpha/beta/rc/dev (e.g. -alpha.1) or pass pypi_pkg_version explicitly."

# Validate each SELECTED ecosystem against the prerelease flag.
check_eco() {
  local eco="$1" v="$2" label="$3"
  if is_prerelease_for "$eco" "$v"; then
    [[ "$PRERELEASE" == "true" ]] \
      || die "$label version '$v' is a prerelease but prerelease=false. Set prerelease=true or use a stable version."
  else
    [[ "$PRERELEASE" == "false" ]] \
      || die "$label version '$v' is NOT a prerelease but prerelease=true. Prerelease mode must not publish a stable version (it would become the default install)."
  fi
}

[[ -n "${SELECTED[typescript]:-}" ]] && check_eco npm "$NPM_VERSION" "npm (TypeScript SDK)"
[[ -n "${SELECTED[analytics]:-}" ]]  && check_eco npm "$NPM_VERSION" "npm (Analytics API Client)"
[[ -n "${SELECTED[python]:-}" ]]     && check_eco pypi "$PYPI_VERSION" "PyPI (Python SDK)"
[[ -n "${SELECTED[ruby]:-}" ]]       && check_eco rubygems "$RUBYGEMS_VERSION" "RubyGems (Ruby SDK)"

# Maven guardrail: Maven Central is immutable and consumers cannot opt out of
# prereleases. The only safe prerelease channel is a -SNAPSHOT to a snapshots
# repository, so a non-SNAPSHOT prerelease must be blocked outright.
if [[ -n "${SELECTED[java]:-}" ]]; then
  if [[ "$PRERELEASE" == "true" ]]; then
    [[ "$MAVEN_VERSION" == *SNAPSHOT* ]] \
      || die "Java/Maven has no safe prerelease channel: Maven Central is immutable and has no prerelease opt-out. For a prerelease, either omit 'java' from packages, or pass maven_pkg_version as a '-SNAPSHOT' version targeting a snapshots repository. Refusing to upload '$MAVEN_VERSION' to Maven Central."
  else
    [[ "$MAVEN_VERSION" != *SNAPSHOT* && "$MAVEN_VERSION" != *-* ]] \
      || die "Stable release selected but Maven version '$MAVEN_VERSION' looks like a prerelease/SNAPSHOT."
  fi
fi

# ----------------------------------------------------------------------------
# npm dist-tag: derive + guard
# ----------------------------------------------------------------------------
if [[ -n "${NPM_TAG_OVERRIDE:-}" ]]; then
  NPM_TAG="$NPM_TAG_OVERRIDE"
elif [[ "$PRERELEASE" == "true" ]]; then
  NPM_TAG="${SUFFIX%%.*}"          # alpha / beta / rc / dev
else
  NPM_TAG="latest"
fi

if [[ "$PRERELEASE" == "true" && "$NPM_TAG" == "latest" ]]; then
  die "prerelease=true but npm dist-tag resolved to 'latest'. A prerelease must never publish to the 'latest' channel."
fi
if [[ "$PRERELEASE" == "false" && "$NPM_TAG" != "latest" ]]; then
  die "prerelease=false but npm dist-tag is '$NPM_TAG'. A stable release must publish to 'latest'."
fi

# ----------------------------------------------------------------------------
# Build the dynamic matrix (publishable nx projects only; CLI is separate)
# ----------------------------------------------------------------------------
MATRIX_JSON="$(
  for t in "${!SELECTED[@]}"; do
    IFS='|' read -r name projects eco <<< "${TOKEN_META[$t]}"
    [[ "$t" == "cli" ]] && continue
    printf '%s\t%s\n' "$name" "$projects"
  done | sort | jq -R -s -c 'split("\n") | map(select(length>0) | split("\t") | {name: .[0], projects: .[1]}) | {include: .}'
)"

PUBLISH_CLI="false"
[[ -n "${SELECTED[cli]:-}" ]] && PUBLISH_CLI="true"

HAS_PUBLISHABLE="false"
[[ "$MATRIX_JSON" != '{"include":[]}' ]] && HAS_PUBLISHABLE="true"

# ----------------------------------------------------------------------------
# Emit + summarize
# ----------------------------------------------------------------------------
emit matrix "$MATRIX_JSON"
emit npm_version "$NPM_VERSION"
emit pypi_version "$PYPI_VERSION"
emit rubygems_version "$RUBYGEMS_VERSION"
emit maven_version "$MAVEN_VERSION"
emit cli_version "$CLI_VERSION"
emit npm_tag "$NPM_TAG"
emit publish_cli "$PUBLISH_CLI"
emit has_publishable "$HAS_PUBLISHABLE"

{
  echo "Resolved publish plan (prerelease=$PRERELEASE):"
  echo "  selected : ${!SELECTED[*]}"
  [[ -n "${SELECTED[typescript]:-}" || -n "${SELECTED[analytics]:-}" ]] && echo "  npm      : $NPM_VERSION  (dist-tag: $NPM_TAG)"
  [[ -n "${SELECTED[python]:-}" ]] && echo "  pypi     : $PYPI_VERSION"
  [[ -n "${SELECTED[ruby]:-}" ]]   && echo "  rubygems : $RUBYGEMS_VERSION"
  [[ -n "${SELECTED[java]:-}" ]]   && echo "  maven    : $MAVEN_VERSION"
  [[ "$PUBLISH_CLI" == "true" ]]   && echo "  cli      : $CLI_VERSION  (Homebrew tap update: $([[ "$PRERELEASE" == "false" ]] && echo yes || echo SKIPPED))"
  echo "  matrix   : $MATRIX_JSON"
} >&2
