#!/usr/bin/env python3
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
"""Fail on a breaking change against the last stable release.

Compares the working tree with the most recent stable release tag (the
highest ``vX.Y.Z`` with ``X >= 2`` and no pre-release suffix):

- the committed public-surface snapshots (``sdk-typescript/api-surface/``,
  ``sdk-python/api-surface/``, ``cli/api-surface/``): a removed declaration,
  command, flag or environment variable, or a changed signature that is not a
  known-compatible change (an added optional parameter, a widened union, an
  added base class);
- ``contract/*.json``: the OpenAPI contracts with oasdiff (``ERR`` level;
  enums the contract tags ``x-daytona-open-enum`` may gain values), the error
  catalogs (a removed code, a changed status) and the other contract files (a
  removed field or a changed identifier).

A breaking change passes only when a ``changelog.d/*.breaking.md`` fragment
announces it; the maintainer then decides the major bump. With ``--base REV``
(a pull request's base), only the breaking changes that are new since REV
count, and the fragment must be added since REV; without it, every breaking
change since the release counts and any unreleased breaking fragment
satisfies the check.

    python3 scripts/breaking/breaking_changes.py [--base REV] [--baseline TAG]

Needs the release tags (``git fetch --tags``, or ``--fetch origin`` in a
shallow clone) and oasdiff on PATH, or its
path in ``OASDIFF`` (``scripts/breaking/install-oasdiff.sh`` installs the
pinned release).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence, TextIO

SURFACE_DIRS = ("sdk-typescript/api-surface", "sdk-python/api-surface", "cli/api-surface")
OPENAPI = ("contract/api-openapi.json", "contract/toolbox-openapi.json")
CATALOGS = ("contract/proxy-errors.json", "contract/router-errors.json")
STABLE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# Lines whose kind lists one member of a set: losing one is a removal.
SET_KINDS = ("union-member", "tuple-element", "element", "item", "alias", "enum-member")
# Not public API (docs/VERSIONING.md): reported, never failing.
INFORMATIONAL_KINDS = ("internal-constructor",)
OPENS = "([{<"
CLOSES = ")]}>"


@dataclass
class Report:
    breaking: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


# --- git ----------------------------------------------------------------------


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, check=check, capture_output=True, text=True)


def latest_stable_tag(root: Path, remote: str | None = None) -> str | None:
    """The highest ``vX.Y.Z`` (X >= 2) among the local tags, or a remote's."""
    if remote is None:
        tags = git(root, "tag", "--list", "v*").stdout.split()
    else:
        refs = git(root, "ls-remote", "--tags", "--refs", remote, "v*").stdout.split()
        tags = [ref.removeprefix("refs/tags/") for ref in refs if ref.startswith("refs/tags/")]
    best: tuple[tuple[int, int, int], str] | None = None
    for tag in tags:
        match = STABLE_TAG.match(tag)
        if not match:
            continue
        version = (int(match[1]), int(match[2]), int(match[3]))
        if version[0] >= 2 and (best is None or version > best[0]):
            best = (version, tag)
    return best[1] if best else None


Reader = Callable[[str], "str | None"]


def reader_at(root: Path, rev: str) -> Reader:
    def read(path: str) -> str | None:
        result = git(root, "show", f"{rev}:{path}", check=False)
        return result.stdout if result.returncode == 0 else None

    return read


def worktree_reader(root: Path) -> Reader:
    def read(path: str) -> str | None:
        target = root / path
        return target.read_text(encoding="utf-8") if target.is_file() else None

    return read


def files_at(root: Path, rev: str | None, directory: str, suffix: str) -> set[str]:
    if rev is None:
        base = root / directory
        return {f"{directory}/{p.name}" for p in base.glob(f"*{suffix}")} if base.is_dir() else set()
    out = git(root, "ls-tree", "--name-only", f"{rev}:{directory}", check=False)
    if out.returncode != 0:
        return set()
    return {f"{directory}/{name}" for name in out.stdout.split() if name.endswith(suffix)}


# --- surface snapshots --------------------------------------------------------


def parse_surface(text: str) -> dict[str, list[str]]:
    entries: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        key, _, rest = line.partition(": ")
        entries.setdefault(key, []).append(rest)
    return entries


def split_top(text: str, sep: str) -> list[str]:
    """Split on ``sep`` outside brackets and string literals."""
    parts, depth, quote, start, i = [], 0, "", 0, 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "'\"`":
            quote = ch
        elif ch in OPENS:
            depth += 1
        elif ch in CLOSES and not (ch == ">" and i > 0 and text[i - 1] == "="):
            depth -= 1
        elif depth == 0 and text.startswith(sep, i):
            parts.append(text[start:i].strip())
            start = i + len(sep)
            i += len(sep)
            continue
        i += 1
    parts.append(text[start:].strip())
    return [p for p in parts if p]


def matching_paren(text: str, open_at: int) -> int:
    depth, quote = 0, ""
    for i in range(open_at, len(text)):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"`":
            quote = ch
        elif ch in OPENS:
            depth += 1
        elif ch in CLOSES and not (ch == ">" and text[i - 1] == "="):
            depth -= 1
            if depth == 0:
                return i
    return -1


def is_optional_param(param: str) -> bool:
    name = param.split(":", 1)[0].split("=", 1)[0].strip()
    return (
        param == "*"
        or param.startswith("*")
        or param.startswith("...")
        or name.endswith("?")
        or " = " in param
    )


def param_compatible(old: str, new: str) -> bool:
    if old == new:
        return True
    if not is_optional_param(old) and new.startswith(old + " = "):
        return True  # a required parameter gained a default
    name, sep, rest = old.partition(":")
    return bool(sep) and new == f"{name}?:{rest}"  # became optional (TypeScript)


def is_star(param: str) -> bool:
    return param == "*" or (param.startswith("*") and not param.startswith("**"))


def compatible_call(old: str, new: str) -> bool:
    """``new`` accepts every call ``old`` accepted: same prefix and return type,
    old parameters kept in order (a parameter may gain a default), and every
    added parameter optional and placed where it shifts no positional one."""
    o, n = old.find("("), new.find("(")
    if o < 0 or n < 0 or old[:o] != new[:n]:
        return False
    oc, nc = matching_paren(old, o), matching_paren(new, n)
    if oc < 0 or nc < 0 or old[oc + 1 :] != new[nc + 1 :]:
        return False
    old_params, new_params = split_top(old[o + 1 : oc], ","), split_top(new[n + 1 : nc], ",")
    # Old parameters from here on are keyword-only (Python): an insertion
    # among them shifts nothing.
    keyword_from = next((i + 1 for i, p in enumerate(old_params) if is_star(p)), len(old_params))
    j = 0
    for param in new_params:
        if j < len(old_params) and param_compatible(old_params[j], param):
            j += 1
            continue
        if not is_optional_param(param) or j < min(keyword_from, len(old_params)):
            return False
    return j == len(old_params)


def union_members(detail: str) -> list[str]:
    if not detail.startswith("= "):
        return []
    body = detail[2:].strip()
    literal = re.fullmatch(r"Literal\[(.*)\]", body)
    return split_top(literal[1], ",") if literal else split_top(body, "|")


CLASS_LINE = re.compile(r"([a-z][a-z ]*?) \((.*)\)")


def compatible_change(old: str, new: str) -> bool:
    """A known-compatible change of one line's rest (kind + signature)."""
    if compatible_call(old, new):
        return True
    old_kind, new_kind = old.split(" ", 1)[0], new.split(" ", 1)[0]
    # A union or Literal alias that only gained members.
    if old_kind == new_kind == "type":
        old_m, new_m = union_members(old.partition(" ")[2]), union_members(new.partition(" ")[2])
        if old_m and set(old_m) <= set(new_m):
            return True
    # A Python class that only gained base classes.
    o, n = CLASS_LINE.fullmatch(old), CLASS_LINE.fullmatch(new)
    return bool(o and n and o[1] == n[1] and set(split_top(o[2], ",")) <= set(split_top(n[2], ",")))


def informational(rest: str) -> bool:
    return any(word in INFORMATIONAL_KINDS for word in rest.split(" ")[:3])


def is_child(key: str, parents: Iterable[str]) -> bool:
    return any(key.startswith(p + sep) for p in parents for sep in (".", "[", " "))


def compare_surface(name: str, old_text: str, new_text: str | None, report: Report) -> None:
    old = parse_surface(old_text)
    new = parse_surface(new_text or "")
    if new_text is None:
        report.breaking.append(f"{name}: snapshot removed (every declaration it listed)")
        return
    removed_keys: list[str] = []
    for key in sorted(old):
        news = new.get(key)
        if news is None:
            if is_child(key, removed_keys):
                continue
            removed_keys.append(key)
            if all(informational(r) for r in old[key]):
                report.notices.append(f"{name}: removed {key}: {old[key][0]}")
            else:
                report.breaking.append(f"{name}: removed {key}: {old[key][0]}")
            continue
        for rest in old[key]:
            if rest in news:
                continue
            line = f"{key}: {rest}"
            if informational(rest):
                report.notices.append(f"{name}: changed (not public API) {line}")
            elif rest.split(" ", 1)[0] in SET_KINDS:
                report.breaking.append(f"{name}: removed {line}")
            elif any(compatible_change(rest, candidate) for candidate in news):
                report.notices.append(f"{name}: compatible change of {line}")
            else:
                now = " | ".join(f"{key}: {r}" for r in news if r not in old[key]) or "(see snapshot)"
                report.breaking.append(f"{name}: changed {line}  ->  {now}")


# --- contracts ----------------------------------------------------------------


def open_enums_extensible(node: object) -> object:
    """Enums tagged x-daytona-open-enum may grow: give oasdiff the
    x-extensible-enum form, which allows added values but not removed ones."""
    if isinstance(node, dict):
        out = {k: open_enums_extensible(v) for k, v in node.items()}
        if "enum" in out and out.get("x-daytona-open-enum"):
            out["x-extensible-enum"] = out.pop("enum")
        return out
    if isinstance(node, list):
        return [open_enums_extensible(v) for v in node]
    return node


def oasdiff_findings(oasdiff: str, path: str, old_text: str, new_text: str) -> tuple[set[str], set[str]]:
    with tempfile.TemporaryDirectory() as tmp:
        base, rev = Path(tmp, "base.json"), Path(tmp, "revision.json")
        base.write_text(json.dumps(open_enums_extensible(json.loads(old_text))))
        rev.write_text(json.dumps(open_enums_extensible(json.loads(new_text))))
        result = subprocess.run(
            [oasdiff, "breaking", str(base), str(rev), "--format", "json", "--allow-external-refs=false"],
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        raise SystemExit(f"oasdiff failed on {path}: {result.stderr.strip()}")
    errors, warnings = set(), set()
    for item in json.loads(result.stdout or "[]"):
        where = " ".join(p for p in (item.get("operation"), item.get("path")) if p)
        text = f"{path}: [{item.get('id')}] {where}: {item.get('text')}"
        (errors if item.get("level", 0) >= 3 else warnings).add(text)
    return errors, warnings


def catalog_findings(path: str, old_text: str, new_text: str) -> set[str]:
    def index(doc: dict[str, object]) -> dict[str, dict[str, object]]:
        out: dict[str, dict[str, object]] = {}
        for group in ("codes", "generic"):
            for entry in doc.get(group, []) or []:  # type: ignore[union-attr]
                out[f"{group}:{entry['code']}"] = entry
        return out

    old, new = index(json.loads(old_text)), index(json.loads(new_text))
    found = set()
    for key, entry in old.items():
        if key not in new:
            found.add(f"{path}: error code {key} removed")
            continue
        if entry.get("status") != new[key].get("status"):
            found.add(f"{path}: error code {key} status {entry.get('status')} -> {new[key].get('status')}")
        for extra in set(map(str, entry.get("extras") or [])) - set(map(str, new[key].get("extras") or [])):
            found.add(f"{path}: error code {key} lost extra {extra}")
    return found


def flatten(node: object, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            if not str(key).startswith("$"):
                out.update(flatten(value, f"{prefix}/{key}"))
    elif isinstance(node, list):
        if node and all(isinstance(v, dict) and "name" in v for v in node):
            for value in node:
                out[f"{prefix}[{value['name']}]"] = "{...}"
                out.update(flatten(value, f"{prefix}[{value['name']}]"))
        elif all(not isinstance(v, (dict, list)) for v in node):
            for value in node:
                out[f"{prefix}[]={json.dumps(value)}"] = True
        else:
            for i, value in enumerate(node):
                out.update(flatten(value, f"{prefix}[{i}]"))
    else:
        out[prefix] = node
    return out


def json_findings(path: str, old_text: str, new_text: str) -> set[str]:
    """A removed field, or a changed value that is not prose."""
    old, new = flatten(json.loads(old_text)), flatten(json.loads(new_text))
    found = set()
    removed: list[str] = []
    for key, value in sorted(old.items()):
        if key not in new:
            if not any(key.startswith(parent + sep) for parent in removed for sep in "/["):
                found.add(f"{path}: {key} removed")
            removed.append(key)
        elif new[key] != value and not (isinstance(value, str) and " " in value):
            found.add(f"{path}: {key} changed {json.dumps(value)} -> {json.dumps(new[key])}")
    return found


# --- the check ----------------------------------------------------------------


def findings(root: Path, baseline: str, side: str | None, oasdiff: Callable[[], str], report: Report) -> set[str]:
    """Breaking findings from ``baseline`` to ``side`` (a revision, or None
    for the working tree). Notices go to ``report`` only for the head side."""
    read_old = reader_at(root, baseline)
    read_new = worktree_reader(root) if side is None else reader_at(root, side)
    local = Report()
    for directory in SURFACE_DIRS:
        for path in sorted(files_at(root, baseline, directory, ".api.txt")):
            old_text = read_old(path)
            if old_text is not None:
                compare_surface(path, old_text, read_new(path), local)
    found = set(local.breaking)
    for path in sorted(files_at(root, baseline, "contract", ".json")):
        old_text, new_text = read_old(path), read_new(path)
        if old_text is None:
            continue
        if new_text is None:
            found.add(f"{path}: removed")
        elif old_text == new_text:
            continue
        elif path in OPENAPI:
            errors, warnings = oasdiff_findings(oasdiff(), path, old_text, new_text)
            found |= errors
            local.notices.extend(sorted(warnings))
        elif path in CATALOGS:
            found |= catalog_findings(path, old_text, new_text)
        else:
            found |= json_findings(path, old_text, new_text)
    if side is None:
        report.notices.extend(local.notices)
    return found


def breaking_fragments(root: Path, since: str) -> list[str]:
    present = sorted(p.relative_to(root).as_posix() for p in (root / "changelog.d").glob("*.breaking.md"))
    before = files_at(root, since, "changelog.d", ".breaking.md")
    return [p for p in present if p not in before]


def find_oasdiff() -> str:
    candidate = os.environ.get("OASDIFF") or shutil.which("oasdiff")
    if not candidate:
        raise SystemExit(
            "oasdiff not found: install the pinned release with scripts/breaking/install-oasdiff.sh "
            "and put it on PATH, or set OASDIFF to its path"
        )
    return candidate


def main(argv: Sequence[str] | None = None, out: TextIO = sys.stdout) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", help="count only breaking changes new since this revision (a pull request's base)")
    parser.add_argument("--baseline", help="compare with this tag instead of the latest stable release")
    parser.add_argument(
        "--fetch",
        metavar="REMOTE",
        help="find the release tag on REMOTE and fetch only its commit (for shallow CI clones)",
    )
    parser.add_argument("--root", default=".", help="repository root (default: the current directory)")
    args = parser.parse_args(argv)
    root = Path(git(Path(args.root), "rev-parse", "--show-toplevel").stdout.strip())

    baseline = args.baseline or latest_stable_tag(root, args.fetch)
    if baseline is not None and args.fetch:
        git(root, "fetch", "--quiet", "--no-tags", "--depth=1", args.fetch, f"+refs/tags/{baseline}:refs/tags/{baseline}")
    if baseline is None:
        print("no stable release yet (no vX.Y.Z tag with X >= 2): nothing to compare against.", file=out)
        return 0
    if git(root, "rev-parse", "--verify", "--quiet", f"{baseline}^{{commit}}", check=False).returncode != 0:
        raise SystemExit(f"baseline {baseline} is not a commit here (fetch the tags: git fetch --tags)")
    if not any(files_at(root, baseline, d, ".api.txt") for d in SURFACE_DIRS):
        print(f"note: {baseline} carries no API surface snapshots; only contract/ is compared.", file=out)

    report = Report()
    head = findings(root, baseline, None, find_oasdiff, report)
    if args.base:
        earlier = findings(root, baseline, args.base, find_oasdiff, Report())
        new = sorted(head - earlier)
        accepted = sorted(head & earlier)
        fragments = breaking_fragments(root, args.base)
        scope = f"new since {args.base}"
    else:
        new, accepted = sorted(head), []
        fragments = breaking_fragments(root, baseline)
        scope = f"since {baseline}"

    print(f"Breaking-change check against {baseline}", file=out)
    for line in report.notices:
        print(f"  note: {line}", file=out)
    if accepted:
        print(f"{len(accepted)} breaking change(s) since {baseline} were already on the base branch:", file=out)
        for line in accepted:
            print(f"  = {line}", file=out)
    if not new:
        print(f"No breaking change {scope}.", file=out)
        return 0
    print(f"{len(new)} breaking change(s) {scope}:", file=out)
    for line in new:
        print(f"  ! {line}", file=out)
    if fragments:
        print(f"Announced by {', '.join(fragments)}: the next release is a major one (the maintainer decides).", file=out)
        return 0
    print(
        f"\nThese break code written against {baseline}. Restore compatibility, or announce the break "
        "with a changelog.d/<slug>.breaking.md fragment (changelog.d/README.md); the maintainer then "
        "decides the major version bump. A compatible change flagged here (the check is conservative "
        "about changed signatures) also needs the maintainer's call.",
        file=out,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
