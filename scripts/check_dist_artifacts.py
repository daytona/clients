#!/usr/bin/env python3
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Prove built SDK artifacts carry what the publish relies on.

    check_dist_artifacts.py python <dist-dir> [--v0]
    check_dist_artifacts.py npm <pack-dir> [--v0]

Reads each archive's member list with zipfile / tarfile (no `lister | grep -q`
pipeline, which under `set -o pipefail` fails on a match once the lister gets
SIGPIPE) and matches exact member paths. Used by sdk_publish.yaml before
upload and by sdk-ci on the artifacts its bundle smokes build.
"""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path


def _one(directory: Path, pattern: str) -> Path:
    found = sorted(directory.glob(pattern))
    if len(found) != 1:
        raise SystemExit(f"::error::expected one {pattern} in {directory}, found {[p.name for p in found]}")
    return found[0]


def _require(members: set[str], wanted: str, what: str) -> list[str]:
    return [] if wanted in members else [f"{what} lacks {wanted}"]


def _require_suffix(members: set[str], suffix: str, what: str) -> list[str]:
    return [] if any(m.endswith(suffix) for m in members) else [f"{what} lacks *{suffix}"]


def check_python(dist: Path, v0: bool) -> list[str]:
    with zipfile.ZipFile(_one(dist, "*.whl")) as wheel:
        wheel_members = set(wheel.namelist())
    with tarfile.open(_one(dist, "*.tar.gz")) as sdist:
        sdist_members = set(sdist.getnames())
    errors = _require(wheel_members, "daytona/py.typed", "wheel")
    errors += _require_suffix(sdist_members, "/src/daytona/py.typed", "sdist")
    if v0:
        errors += _require(wheel_members, "daytona/v0/_bundle.py", "wheel")
        errors += _require_suffix(sdist_members, "/src/daytona/v0/_bundle.py", "sdist")
    return errors


def check_npm(pack: Path, v0: bool) -> list[str]:
    with tarfile.open(_one(pack, "*.tgz")) as tarball:
        members = set(tarball.getnames())
    errors = _require(members, "package/package.json", "npm tarball")
    if v0:
        errors += _require(members, "package/dist/cjs/v0/_bundle.js", "npm tarball")
        errors += _require(members, "package/dist/esm/v0/index.js", "npm tarball")
    return errors


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--v0"]
    if len(args) != 2 or args[0] not in ("python", "npm"):
        print(__doc__, file=sys.stderr)
        return 2
    kind, directory = args[0], Path(args[1])
    errors = (check_python if kind == "python" else check_npm)(directory, "--v0" in argv)
    for error in errors:
        print(f"::error::{error}")
    if not errors:
        print(f"{kind} artifacts in {directory}: ok")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
