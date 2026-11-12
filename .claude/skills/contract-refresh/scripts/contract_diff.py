#!/usr/bin/env python3
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
"""Summarize how contract/{api,toolbox}-openapi.json differ from a base commit.

Usage: contract_diff.py [base-ref]

The working tree is compared with the merge base of HEAD and base-ref
(default origin/main), so contract files already committed on the PR branch
still show up. Prints, per file, the paths and schemas added, removed and
changed (present on both sides with a different definition).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

FILES = ("api", "toolbox")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def load_at(rev: str, path: str) -> dict[str, Any]:
    try:
        spec: dict[str, Any] = json.loads(git("show", f"{rev}:{path}"))
    except subprocess.CalledProcessError:
        return {}
    return spec


def diff_keys(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "added": sorted(set(new) - set(old)),
        "removed": sorted(set(old) - set(new)),
        "changed": sorted(k for k in set(old) & set(new) if old[k] != new[k]),
    }


def schemas(spec: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = spec.get("components", {}).get("schemas", {})
    return found


def main(argv: list[str]) -> int:
    if argv[:1] in (["-h"], ["--help"]):
        print(__doc__)
        return 0
    if len(argv) > 1:
        print(__doc__, file=sys.stderr)
        return 2
    root = Path(git("rev-parse", "--show-toplevel").strip())
    base = git("merge-base", "HEAD", argv[0] if argv else "origin/main").strip()
    print(f"contract diff against merge base {base[:12]}")
    for name in FILES:
        path = f"contract/{name}-openapi.json"
        old = load_at(base, path)
        new = json.loads((root / path).read_text())
        for kind, (o, n) in {
            "paths": (old.get("paths", {}), new.get("paths", {})),
            "schemas": (schemas(old), schemas(new)),
        }.items():
            for change, keys in diff_keys(o, n).items():
                print(f"{name} {kind} {change}: {keys}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
