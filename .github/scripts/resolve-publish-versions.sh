#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Resolve which language ecosystems to publish and validate the prerelease
# contract for the SDK & CLI publish workflow.
#
# VERSION (optional, must start with 'v') is the default for every package. Its
# leading 'v' is stripped for npm/PyPI/RubyGems/Maven and KEPT for the CLI. A
# per-language override replaces that default for one ecosystem. A language
# publishes iff it has an effective version (override, or the VERSION default).
# Versions are used verbatim per ecosystem; no cross-format conversion is done.
#
# Inputs (environment variables, any may be empty):
#   VERSION            canonical default, e.g. v0.190.0
#   NPM_OVERRIDE       npm (publishes sdk-typescript + analytics-api-client)
#   PYPI_OVERRIDE      PyPI (publishes sdk-python)
#   RUBYGEMS_OVERRIDE  RubyGems (publishes sdk-ruby)
#   MAVEN_OVERRIDE     Maven (publishes sdk-java)
#   CLI_OVERRIDE       CLI (updates Homebrew tap)
#   NPM_TAG            npm dist-tag: latest | rc | alpha | beta
#   PRERELEASE         'true' | 'false'
#
# Outputs (to $GITHUB_OUTPUT, or stdout when run locally):
#   matrix, npm_version, pypi_version, rubygems_version, maven_version,
#   cli_version, npm_tag, publish_cli, has_publishable

set -euo pipefail

die() { echo "::error::$*" >&2; exit 1; }

is_npm_pre()   { [[ "$1" == *-* ]]; }
is_pypi_pre()  { [[ "$1" =~ [0-9](a|b|rc)[0-9]+ ]] || [[ "$1" == *.dev* ]]; }
is_ruby_pre()  { [[ "$1" =~ [a-zA-Z] ]]; }
is_maven_pre() { [[ "$1" == *SNAPSHOT* ]] || [[ "$1" == *-* ]]; }

OUT_FILE="${GITHUB_OUTPUT:-$(mktemp)}"
emit() { printf '%s=%s\n' "$1" "$2" >> "$OUT_FILE"; }

VERSION="${VERSION:-}"
NPM_OVERRIDE="${NPM_OVERRIDE:-}"
PYPI_OVERRIDE="${PYPI_OVERRIDE:-}"
RUBYGEMS_OVERRIDE="${RUBYGEMS_OVERRIDE:-}"
MAVEN_OVERRIDE="${MAVEN_OVERRIDE:-}"
CLI_OVERRIDE="${CLI_OVERRIDE:-}"
NPM_TAG="${NPM_TAG:-latest}"
PRERELEASE="${PRERELEASE:-false}"

[[ "$PRERELEASE" == "true" || "$PRERELEASE" == "false" ]] \
  || die "PRERELEASE must be 'true' or 'false' (got '$PRERELEASE')"
case "$NPM_TAG" in latest|rc|alpha|beta) ;; *) die "npm_tag must be one of latest|rc|alpha|beta (got '$NPM_TAG')" ;; esac

# Canonical default: strip the leading 'v' for the package ecosystems; the CLI
# keeps the 'v'.
BASE=""
if [[ -n "$VERSION" ]]; then
  [[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$ ]] \
    || die "Invalid version format: $VERSION (expected vX.Y.Z or vX.Y.Z-suffix)"
  BASE="${VERSION#v}"
fi

NPM_VERSION="${NPM_OVERRIDE:-$BASE}"
PYPI_VERSION="${PYPI_OVERRIDE:-$BASE}"
RUBYGEMS_VERSION="${RUBYGEMS_OVERRIDE:-$BASE}"
MAVEN_VERSION="${MAVEN_OVERRIDE:-$BASE}"
CLI_VERSION="${CLI_OVERRIDE:-$VERSION}"

if [[ -z "$NPM_VERSION$PYPI_VERSION$RUBYGEMS_VERSION$MAVEN_VERSION$CLI_VERSION" ]]; then
  die "Nothing to publish: set 'version' to release everything, or set at least one language version."
fi

# Per-ecosystem prerelease contract: in prerelease mode nothing may reach a
# stable/default channel; in stable mode nothing may look like a prerelease.
if [[ "$PRERELEASE" == "true" ]]; then
  if [[ -n "$NPM_VERSION" ]]; then
    [[ "$NPM_TAG" != "latest" ]] || die "prerelease: npm tag must not be 'latest' (pick rc/alpha/beta)."
    is_npm_pre "$NPM_VERSION" || die "prerelease: npm version '$NPM_VERSION' is not a prerelease (e.g. 0.190.0-alpha.3)."
  fi
  [[ -n "$PYPI_VERSION" ]] && { is_pypi_pre "$PYPI_VERSION" || die "prerelease: PyPI version '$PYPI_VERSION' is not a PEP 440 prerelease (e.g. 0.192.0a1)."; }
  [[ -n "$RUBYGEMS_VERSION" ]] && { is_ruby_pre "$RUBYGEMS_VERSION" || die "prerelease: RubyGems version '$RUBYGEMS_VERSION' is not a prerelease (must contain a letter, e.g. 0.190.0.alpha.3)."; }
  [[ -n "$MAVEN_VERSION" ]] && { [[ "$MAVEN_VERSION" == *SNAPSHOT* ]] || die "prerelease: Maven version '$MAVEN_VERSION' must be a -SNAPSHOT (Maven Central is immutable and has no prerelease channel, e.g. 0.190.2-SNAPSHOT)."; }
else
  if [[ -n "$NPM_VERSION" ]]; then
    [[ "$NPM_TAG" == "latest" ]] || die "stable: npm tag is '$NPM_TAG' but prerelease is off. Enable prerelease for non-latest tags, or set tag to latest."
    ! is_npm_pre "$NPM_VERSION" || die "stable: npm version '$NPM_VERSION' looks like a prerelease. Enable prerelease."
  fi
  [[ -n "$PYPI_VERSION" ]] && { ! is_pypi_pre "$PYPI_VERSION" || die "stable: PyPI version '$PYPI_VERSION' looks like a prerelease. Enable prerelease."; }
  [[ -n "$RUBYGEMS_VERSION" ]] && { ! is_ruby_pre "$RUBYGEMS_VERSION" || die "stable: RubyGems version '$RUBYGEMS_VERSION' looks like a prerelease. Enable prerelease."; }
  [[ -n "$MAVEN_VERSION" ]] && { ! is_maven_pre "$MAVEN_VERSION" || die "stable: Maven version '$MAVEN_VERSION' looks like a prerelease/SNAPSHOT. Enable prerelease."; }
fi

# Build the publish matrix (one row per selected language; CLI is separate).
ROWS=()
[[ -n "$NPM_VERSION" ]]      && ROWS+=('{"name":"npm (TypeScript + Analytics)","projects":"sdk-typescript,analytics-api-client"}')
[[ -n "$PYPI_VERSION" ]]     && ROWS+=('{"name":"Python SDK","projects":"sdk-python"}')
[[ -n "$RUBYGEMS_VERSION" ]] && ROWS+=('{"name":"Ruby SDK","projects":"sdk-ruby"}')
[[ -n "$MAVEN_VERSION" ]]    && ROWS+=('{"name":"Java SDK","projects":"sdk-java"}')

MATRIX_JSON="$(printf '%s\n' "${ROWS[@]:-}" | jq -s -c '{include: map(select(length>0))}')"

PUBLISH_CLI="false"; [[ -n "$CLI_VERSION" ]] && PUBLISH_CLI="true"
HAS_PUBLISHABLE="false"; [[ "$MATRIX_JSON" != '{"include":[]}' ]] && HAS_PUBLISHABLE="true"

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
  echo "Publish plan (prerelease=$PRERELEASE):"
  [[ -n "$NPM_VERSION" ]]      && echo "  npm      : $NPM_VERSION  (dist-tag: $NPM_TAG)  -> sdk-typescript, analytics-api-client"
  [[ -n "$PYPI_VERSION" ]]     && echo "  pypi     : $PYPI_VERSION  -> sdk-python"
  [[ -n "$RUBYGEMS_VERSION" ]] && echo "  rubygems : $RUBYGEMS_VERSION  -> sdk-ruby"
  [[ -n "$MAVEN_VERSION" ]]    && echo "  maven    : $MAVEN_VERSION  -> sdk-java"
  [[ "$PUBLISH_CLI" == "true" ]] && echo "  cli      : $CLI_VERSION  (Homebrew: $([[ "$PRERELEASE" == "false" ]] && echo yes || echo SKIPPED in prerelease))"
} >&2

exit 0
