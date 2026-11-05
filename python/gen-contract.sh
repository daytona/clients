#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Regenerate the vendored wire models under src/daytona/v2/_gen/{toolbox,api}/
# from the contract artifacts in ../contract/. Models only — pydantic v2 classes from openapi-generator's `python`
# generator. No api classes, no rest.py: the handwritten SDK owns
# HTTP; the generated package is a typing + validation aid at its wire boundary.
#
# Tooling is self-contained in gen/ (npm wrapper + down-converter pinned by
# gen/package-lock.json, jar version pinned in gen/openapitools.json) so this
# script runs from a source checkout. Needs
# node + a JRE on PATH.
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

step() { printf '\n== %s\n' "$*"; }

# openapi-generator-cli downloads its jar itself on first use, silently and
# without a timeout; fetch it up front instead (bounded, retried, announced)
# into the storageDir openapitools.json points at, so the wrapper never
# downloads. The version is the cache key.
GEN_VERSION="$(node -p "require('./gen/openapitools.json')['generator-cli'].version")"
JAR_DIR="$(node -p "require('./gen/openapitools.json')['generator-cli'].storageDir")"
JAR="$JAR_DIR/$GEN_VERSION.jar"

# sha256 of each openapi-generator-cli jar this script may run (Maven Central
# publishes only sha1). Bumping the version in gen/openapitools.json needs a
# new entry here.
generator_jar_sha256() {
  case "$1" in
    7.14.0) echo e03186835022ca02da4aa95e3967b6a3b6d44c2e5f7606e6d5c22466f519c757 ;;
    *) return 1 ;;
  esac
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

JAR_SHA256="$(generator_jar_sha256 "$GEN_VERSION")" || {
  echo "gen-contract: no pinned sha256 for openapi-generator-cli $GEN_VERSION; add it to generator_jar_sha256" >&2
  exit 1
}

# verify_jar FILE: fail unless FILE has the pinned digest.
verify_jar() {
  local got
  got="$(sha256_of "$1")"
  if [ "$got" != "$JAR_SHA256" ]; then
    echo "gen-contract: checksum mismatch for openapi-generator-cli $GEN_VERSION ($1): got $got, want $JAR_SHA256" >&2
    return 1
  fi
}

if [ ! -f "$JAR" ]; then
  step "downloading openapi-generator-cli $GEN_VERSION jar (~30 MiB, cached in $JAR_DIR)"
  mkdir -p "$JAR_DIR"
  rm -f "$JAR.tmp"
  curl -fL --silent --show-error \
    --connect-timeout 20 --max-time 600 --speed-limit 10240 --speed-time 60 \
    --retry 3 --retry-all-errors -o "$JAR.tmp" \
    "https://repo1.maven.org/maven2/org/openapitools/openapi-generator-cli/$GEN_VERSION/openapi-generator-cli-$GEN_VERSION.jar"
  verify_jar "$JAR.tmp" || { rm -f "$JAR.tmp"; exit 1; }
  mv "$JAR.tmp" "$JAR"
fi
# A cached jar is checked on every run too: the cache is not trusted storage.
verify_jar "$JAR" || { echo "gen-contract: delete $JAR and rerun to download it again" >&2; exit 1; }

step "installing generator tooling (npm ci, gen/package-lock.json)"
(cd gen && npm ci --ignore-scripts --no-audit --no-fund --silent \
  --fetch-timeout=60000 --fetch-retries=3)
BIN=gen/node_modules/.bin

# The specs are OpenAPI 3.1; openapi-generator's python generator
# is only reliable on 3.0 input, so it gets a down-converted, ephemeral copy.
# The checked-in contract stays the 3.1 artifact.
step "down-converting contract specs to OpenAPI 3.0"
"$BIN/openapi-down-convert" --input ../contract/toolbox-openapi.json --output gen/.toolbox-3.0.json
"$BIN/openapi-down-convert" --input ../contract/api-openapi.json --output gen/.api-3.0.json
# Credential fields keep their wire value but are redacted from to_str() and
# repr() (gen/mark_sensitive.py; the templates read its markers).
python3 gen/mark_sensitive.py gen/.toolbox-3.0.json gen/.api-3.0.json

export OPENAPI_GENERATOR_CLI_SEARCH_URL=""
for key in toolbox api; do
  step "generating $key models -> src/daytona/v2/_gen/$key/"
  rm -rf "gen/.out/$key"
  (cd . && "$BIN/openapi-generator-cli" generate --openapitools gen/openapitools.json --generator-key "$key" >/dev/null)
  # Keep only the models package; the generator also writes README/docs
  # scaffolding we do not ship, and with models-only generation it emits no
  # package __init__ at all — write them here (deterministically, from the
  # model files) so consumers can write `from daytona.v2._gen.toolbox import X`.
  dest="src/daytona/v2/_gen/$key"
  rm -rf "$dest"
  mkdir -p "$dest"
  cp -r "gen/.out/$key/daytona/v2/_gen/$key/models" "$dest/models"
  python3 - "$dest" "$key" <<'PY'
import pathlib, re, sys
dest, key = pathlib.Path(sys.argv[1]), sys.argv[2]
lines = []
for f in sorted((dest / "models").glob("*.py")):
    if f.name == "__init__.py":
        continue
    src = f.read_text()
    original = src
    # Stock generator quirk: models without a list-typed field still spell
    # `ClassVar[List[str]]` but do not import List.
    # Harmless at import time only because of `from __future__ import
    # annotations`; make the module self-consistent so typing.get_type_hints()
    # and static checkers do not trip over it.
    if "List[" in src and not re.search(r"^from typing import .*\bList\b", src, re.M):
        src = re.sub(r"^(from typing import Any, ClassVar, Dict)", r"\1, List", src, count=1, flags=re.M)
    src = src.replace("if not re.match(", "if not re.fullmatch(")
    src = src.replace(
        '        member._value_ = value\n        return member',
        '        member._value_ = value\n        cls._value2member_map_[value] = member\n        return member',
    )
    if src != original:
        f.write_text(src)
    for cls in re.findall(r"^class (\w+)\(", src, re.M):
        lines.append(f"from daytona.v2._gen.{key}.models.{f.stem} import {cls} as {cls}")
header = ("# Generated by gen-contract.sh from ../contract/" + ("toolbox" if key == "toolbox" else "api")
          + "-openapi.json - do not edit.\n# Wire models (pydantic v2) for the "
          + ("toolbox" if key == "toolbox" else "API") + " surface.\n\n")
(dest / "__init__.py").write_text(header + "\n".join(lines) + "\n")
(dest / "models" / "__init__.py").write_text("")
PY
done

step "done"
