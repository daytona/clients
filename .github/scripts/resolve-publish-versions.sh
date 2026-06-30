#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Resolve which language ecosystems to publish and validate the prerelease
# contract for the SDK & CLI publish workflow.
#
# Selection is by presence: a language publishes iff its version input is set.
# Versions are taken verbatim in each ecosystem's native format (no conversion).
#
# Inputs (environment variables, any may be empty):
#   NPM_PKG_VERSION       npm (publishes sdk-typescript + analytics-api-client)
#   PYPI_PKG_VERSION      PyPI (publishes sdk-python)
#   RUBYGEMS_PKG_VERSION  RubyGems (publishes sdk-ruby)
#   MAVEN_PKG_VERSION     Maven (publishes sdk-java)
#   CLI_VERSION           CLI (updates Homebrew tap)
#   NPM_TAG               npm dist-tag: latest | rc | alpha | beta
#   PRERELEASE            'true' | 'false'
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

NPM_PKG_VERSION="${NPM_PKG_VERSION:-}"
PYPI_PKG_VERSION="${PYPI_PKG_VERSION:-}"
RUBYGEMS_PKG_VERSION="${RUBYGEMS_PKG_VERSION:-}"
MAVEN_PKG_VERSION="${MAVEN_PKG_VERSION:-}"
CLI_VERSION="${CLI_VERSION:-}"
NPM_TAG="${NPM_TAG:-latest}"
PRERELEASE="${PRERELEASE:-false}"

[[ "$PRERELEASE" == "true" || "$PRERELEASE" == "false" ]] \
  || die "PRERELEASE must be 'true' or 'false' (got '$PRERELEASE')"
case "$NPM_TAG" in latest|rc|alpha|beta) ;; *) die "npm_tag must be one of latest|rc|alpha|beta (got '$NPM_TAG')" ;; esac

if [[ -z "$NPM_PKG_VERSION$PYPI_PKG_VERSION$RUBYGEMS_PKG_VERSION$MAVEN_PKG_VERSION$CLI_VERSION" ]]; then
  die "Nothing to publish: set at least one version (npm / pypi / rubygems / maven / cli)."
fi

# Per-ecosystem prerelease contract: in prerelease mode nothing may reach a
# stable/default channel; in stable mode nothing may look like a prerelease.
if [[ "$PRERELEASE" == "true" ]]; then
  if [[ -n "$NPM_PKG_VERSION" ]]; then
    [[ "$NPM_TAG" != "latest" ]] || die "prerelease: npm tag must not be 'latest' (pick rc/alpha/beta)."
    is_npm_pre "$NPM_PKG_VERSION" || die "prerelease: npm version '$NPM_PKG_VERSION' is not a prerelease (e.g. 0.190.0-alpha.3)."
  fi
  [[ -n "$PYPI_PKG_VERSION" ]] && { is_pypi_pre "$PYPI_PKG_VERSION" || die "prerelease: PyPI version '$PYPI_PKG_VERSION' is not a PEP 440 prerelease (e.g. 0.192.0a1)."; }
  [[ -n "$RUBYGEMS_PKG_VERSION" ]] && { is_ruby_pre "$RUBYGEMS_PKG_VERSION" || die "prerelease: RubyGems version '$RUBYGEMS_PKG_VERSION' is not a prerelease (must contain a letter, e.g. 0.190.0.alpha.3)."; }
  [[ -n "$MAVEN_PKG_VERSION" ]] && { [[ "$MAVEN_PKG_VERSION" == *SNAPSHOT* ]] || die "prerelease: Maven version '$MAVEN_PKG_VERSION' must be a -SNAPSHOT (Maven Central is immutable and has no prerelease channel, e.g. 0.190.2-SNAPSHOT)."; }
else
  if [[ -n "$NPM_PKG_VERSION" ]]; then
    [[ "$NPM_TAG" == "latest" ]] || die "stable: npm tag is '$NPM_TAG' but prerelease is off. Enable prerelease for non-latest tags, or set tag to latest."
    ! is_npm_pre "$NPM_PKG_VERSION" || die "stable: npm version '$NPM_PKG_VERSION' looks like a prerelease. Enable prerelease."
  fi
  [[ -n "$PYPI_PKG_VERSION" ]] && { ! is_pypi_pre "$PYPI_PKG_VERSION" || die "stable: PyPI version '$PYPI_PKG_VERSION' looks like a prerelease. Enable prerelease."; }
  [[ -n "$RUBYGEMS_PKG_VERSION" ]] && { ! is_ruby_pre "$RUBYGEMS_PKG_VERSION" || die "stable: RubyGems version '$RUBYGEMS_PKG_VERSION' looks like a prerelease. Enable prerelease."; }
  [[ -n "$MAVEN_PKG_VERSION" ]] && { ! is_maven_pre "$MAVEN_PKG_VERSION" || die "stable: Maven version '$MAVEN_PKG_VERSION' looks like a prerelease/SNAPSHOT. Enable prerelease."; }
fi

# Build the publish matrix (one row per selected language; CLI is separate).
ROWS=()
[[ -n "$NPM_PKG_VERSION" ]]      && ROWS+=('{"name":"npm (TypeScript + Analytics)","projects":"sdk-typescript,analytics-api-client"}')
[[ -n "$PYPI_PKG_VERSION" ]]     && ROWS+=('{"name":"Python SDK","projects":"sdk-python"}')
[[ -n "$RUBYGEMS_PKG_VERSION" ]] && ROWS+=('{"name":"Ruby SDK","projects":"sdk-ruby"}')
[[ -n "$MAVEN_PKG_VERSION" ]]    && ROWS+=('{"name":"Java SDK","projects":"sdk-java"}')

MATRIX_JSON="$(printf '%s\n' "${ROWS[@]:-}" | jq -s -c '{include: map(select(length>0))}')"

PUBLISH_CLI="false"; [[ -n "$CLI_VERSION" ]] && PUBLISH_CLI="true"
HAS_PUBLISHABLE="false"; [[ "$MATRIX_JSON" != '{"include":[]}' ]] && HAS_PUBLISHABLE="true"

emit matrix "$MATRIX_JSON"
emit npm_version "$NPM_PKG_VERSION"
emit pypi_version "$PYPI_PKG_VERSION"
emit rubygems_version "$RUBYGEMS_PKG_VERSION"
emit maven_version "$MAVEN_PKG_VERSION"
emit cli_version "$CLI_VERSION"
emit npm_tag "$NPM_TAG"
emit publish_cli "$PUBLISH_CLI"
emit has_publishable "$HAS_PUBLISHABLE"

{
  echo "Publish plan (prerelease=$PRERELEASE):"
  [[ -n "$NPM_PKG_VERSION" ]]      && echo "  npm      : $NPM_PKG_VERSION  (dist-tag: $NPM_TAG)  -> sdk-typescript, analytics-api-client"
  [[ -n "$PYPI_PKG_VERSION" ]]     && echo "  pypi     : $PYPI_PKG_VERSION  -> sdk-python"
  [[ -n "$RUBYGEMS_PKG_VERSION" ]] && echo "  rubygems : $RUBYGEMS_PKG_VERSION  -> sdk-ruby"
  [[ -n "$MAVEN_PKG_VERSION" ]]    && echo "  maven    : $MAVEN_PKG_VERSION  -> sdk-java"
  [[ "$PUBLISH_CLI" == "true" ]]   && echo "  cli      : $CLI_VERSION  (Homebrew: $([[ "$PRERELEASE" == "false" ]] && echo yes || echo SKIPPED in prerelease))"
} >&2

exit 0
