#!/usr/bin/env python3
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
"""Fail on a breaking change against the last stable release.

Compares the working tree with the most recent stable release tag (the
highest ``vX.Y.Z`` with ``X >= 2`` and no pre-release suffix; with
``--github-repo``, also not a release GitHub flags as pre-release):

- the committed public-surface snapshots (``sdk-typescript/api-surface/``,
  ``sdk-python/api-surface/``, ``cli/api-surface/``, ``upgrade/api-surface/``):
  a removed declaration, command, flag, environment variable, report field or
  value, or a changed signature or type that is not a
  known-compatible change (an added optional parameter, a widened union, an
  added base class);
- ``contract/*.json``: the OpenAPI contracts with oasdiff (``ERR`` and
  ``WARN`` levels, such as a removed request parameter; enums the contract
  tags ``x-daytona-open-enum`` may gain values), the error
  catalogs (a changed source, a removed code, a changed status) and the other
  contract files (a removed field, or a changed value outside the fields
  listed as documentation).

A breaking change passes only when a ``changelog.d/*.breaking.md`` fragment
announces it; the maintainer then decides the major bump. A change the check
reports but that is in fact compatible goes into
``scripts/breaking/compatible-changes.txt`` instead, copied exactly as the
check prints it, with a one-line reason; it then passes in every mode. With ``--base REV``
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
import urllib.request
from fnmatch import fnmatchcase
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence, TextIO

SURFACE_DIRS = (
    "sdk-typescript/api-surface",
    "sdk-python/api-surface",
    "cli/api-surface",
    "upgrade/api-surface",
)
OPENAPI = ("contract/api-openapi.json", "contract/toolbox-openapi.json")
CATALOGS = ("contract/proxy-errors.json", "contract/router-errors.json")
STABLE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# Lines whose kind lists one member of a set: losing one is a removal.
SET_KINDS = ("union-member", "tuple-element", "element", "item", "alias", "enum-member", "value")
# Not public API (docs/VERSIONING.md): reported, never failing.
INFORMATIONAL_KINDS = ("internal-constructor",)
REMOVED = "(removed)"
ACKNOWLEDGMENTS = "scripts/breaking/compatible-changes.txt"
OPENS = "([{<"
CLOSES = ")]}>"


@dataclass(frozen=True, order=True)
class Finding:
    """One breaking change, identified exactly: the snapshot or contract file
    (``area``) and the fields that pin the change down (``old`` / ``new``
    snapshot lines, or ``rule`` / ``at`` / ``text`` for a contract)."""

    area: str
    fields: tuple[tuple[str, str], ...]

    def render(self, indent: str = "    ") -> str:
        lines = [f"- area: {self.area}", *(f"  {key}: {value}" for key, value in self.fields)]
        return "\n".join(indent + line for line in lines)


@dataclass
class Report:
    breaking: list[Finding] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


def snapshot_finding(area: str, old: str, new: Iterable[str] = ()) -> Finding:
    news = sorted(new) or [REMOVED]
    return Finding(area, (("old", old), *(("new", line) for line in news)))


def contract_finding(area: str, rule: str, at: str, text: str = "") -> Finding:
    fields = (("rule", rule), ("at", at), *((("text", text),) if text else ()))
    return Finding(area, fields)


# --- git ----------------------------------------------------------------------


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, check=check, capture_output=True, text=True)


def prerelease_tags(repo: str, token: str | None) -> set[str]:
    """Tags whose GitHub release is flagged pre-release or draft: not a
    stable release even with a ``vX.Y.Z`` name (the installers skip them too).
    A token with read access sees no drafts; a draft's tag normally does not
    exist until the release is published."""
    flagged: set[str] = set()
    page = 1
    while True:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}",
            headers={"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            releases = json.loads(response.read())
        for release in releases:
            if release.get("prerelease") or release.get("draft"):
                flagged.add(str(release.get("tag_name")))
        if len(releases) < 100:
            return flagged
        page += 1


def latest_stable_tag(root: Path, remote: str | None = None, skip: Iterable[str] = ()) -> str | None:
    """The highest ``vX.Y.Z`` (X >= 2) among the local tags, or a remote's,
    leaving out the tags in ``skip``."""
    skipped = set(skip)
    if remote is None:
        tags = git(root, "tag", "--list", "v*").stdout.split()
    else:
        refs = git(root, "ls-remote", "--tags", "--refs", remote, "v*").stdout.split()
        tags = [ref.removeprefix("refs/tags/") for ref in refs if ref.startswith("refs/tags/")]
    best: tuple[tuple[int, int, int], str] | None = None
    for tag in tags:
        match = STABLE_TAG.match(tag)
        if not match or tag in skipped:
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
        report.breaking.append(snapshot_finding(name, "(the whole snapshot)"))
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
                report.breaking.append(snapshot_finding(name, f"{key}: {old[key][0]}"))
            continue
        for rest in old[key]:
            if rest in news:
                continue
            line = f"{key}: {rest}"
            if informational(rest):
                report.notices.append(f"{name}: changed (not public API) {line}")
            elif rest.split(" ", 1)[0] in SET_KINDS:
                report.breaking.append(snapshot_finding(name, line))
            elif any(compatible_change(rest, candidate) for candidate in news):
                report.notices.append(f"{name}: compatible change of {line}")
            else:
                added = [f"{key}: {r}" for r in news if r not in old[key]]
                report.breaking.append(snapshot_finding(name, line, added))


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


def oasdiff_findings(oasdiff: str, path: str, old_text: str, new_text: str) -> tuple[set[Finding], set[str]]:
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
    errors: set[Finding] = set()
    warnings: set[str] = set()
    for item in json.loads(result.stdout or "[]"):
        where = " ".join(p for p in (item.get("operation"), item.get("path")) if p) or "-"
        # WARN (2) counts too: it covers removed request parameters and
        # properties, which an older SDK may still send.
        if item.get("level", 0) >= 2:
            errors.add(contract_finding(path, str(item.get("id")), where, " ".join(str(item.get("text")).split())))
        else:
            warnings.add(f"{path}: [{item.get('id')}] {where}: {item.get('text')}")
    return errors, warnings


def catalog_findings(path: str, old_text: str, new_text: str) -> set[Finding]:
    def index(doc: dict[str, object]) -> dict[str, dict[str, object]]:
        out: dict[str, dict[str, object]] = {}
        for group in ("codes", "generic"):
            for entry in doc.get(group, []) or []:  # type: ignore[union-attr]
                out[f"{group}:{entry['code']}"] = entry
        return out

    old_doc, new_doc = json.loads(old_text), json.loads(new_text)
    old, new = index(old_doc), index(new_doc)
    found: set[Finding] = set()
    if old_doc.get("source") != new_doc.get("source"):
        change = f"{json.dumps(old_doc.get('source'))} -> {json.dumps(new_doc.get('source'))}"
        found.add(contract_finding(path, "source-changed", "source", change))
    for key, entry in old.items():
        if key not in new:
            found.add(contract_finding(path, "code-removed", key))
            continue
        if entry.get("status") != new[key].get("status"):
            change = f"{entry.get('status')} -> {new[key].get('status')}"
            found.add(contract_finding(path, "status-changed", key, change))
        for extra in set(map(str, entry.get("extras") or [])) - set(map(str, new[key].get("extras") or [])):
            found.add(contract_finding(path, "extra-removed", key, extra))
    return found


def flatten(node: object, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    if prefix and isinstance(node, (dict, list)):
        # The container itself, so removing an empty one is seen too.
        out[prefix] = "{}" if isinstance(node, dict) else "[]"
    if isinstance(node, dict):
        for key, value in node.items():
            if not str(key).startswith("$"):
                out.update(flatten(value, f"{prefix}/{key}"))
    elif isinstance(node, list):
        if node and all(isinstance(v, dict) and "name" in v for v in node):
            for value in node:
                out.update(flatten(value, f"{prefix}[{value['name']}]"))
        else:
            # Order carries no meaning: each element is one entry, compared whole.
            for value in node:
                out[f"{prefix}[]={json.dumps(value, sort_keys=True)}"] = True
    else:
        out[prefix] = node
    return out


# Documentation fields of a contract file: their wording may change. Every
# other value, any string included, is compared exactly.
DOCUMENTATION = {
    "contract/sandbox-events-protocol.json": (
        "*/semantics",
        "*/rule",
        "/version_rule",
        "/ticket/*/auth",
        "/ticket/upgrade/region",
        "/ticket/*/errors/*",
        "/close_codes/*",
        "/client_rules/*",
    ),
}


def is_documentation(path: str, key: str, value: object) -> bool:
    return isinstance(value, str) and any(fnmatchcase(key, p) for p in DOCUMENTATION.get(path, ()))


def json_findings(path: str, old_text: str, new_text: str) -> set[Finding]:
    """A removed field, or a changed value outside the documentation fields."""
    old, new = flatten(json.loads(old_text)), flatten(json.loads(new_text))
    found: set[Finding] = set()
    removed: list[str] = []
    for key, value in sorted(old.items()):
        if key not in new:
            if not any(key.startswith(parent + sep) for parent in removed for sep in "/["):
                found.add(contract_finding(path, "removed", key))
            removed.append(key)
        elif new[key] != value and not is_documentation(path, key, value):
            found.add(contract_finding(path, "changed", key, f"{json.dumps(value)} -> {json.dumps(new[key])}"))
    return found


# --- the check ----------------------------------------------------------------


def findings(root: Path, baseline: str, side: str | None, oasdiff: Callable[[], str], report: Report) -> set[Finding]:
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
            found.add(contract_finding(path, "file-removed", path))
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
    """Breaking fragments added since ``since``. A fragment that only moved
    (same text under a new name) announces nothing new."""
    present = sorted(p.relative_to(root).as_posix() for p in (root / "changelog.d").glob("*.breaking.md"))
    before = files_at(root, since, "changelog.d", ".breaking.md")
    read_before = reader_at(root, since)
    texts_before = {read_before(p) for p in before}
    return [p for p in present if p not in before and (root / p).read_text(encoding="utf-8") not in texts_before]


SNAPSHOT_KEYS = ("old", "new")
CONTRACT_KEYS = ("rule", "at", "text")


def load_acknowledgments(path: Path) -> dict[Finding, str]:
    """Parse the acknowledgment file: findings that are not breaking, each
    with its reason. A malformed entry is an error (exit 1)."""
    if not path.is_file():
        return {}
    where = path.name
    entries: list[tuple[int, list[tuple[str, str]]]] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- "):
            entries.append((number, []))
            line = line[2:]
        elif not entries:
            raise SystemExit(f"{where}:{number}: an entry starts with '- area: '")
        key, sep, value = line.partition(": ")
        if not sep or not value.strip():
            raise SystemExit(f"{where}:{number}: expected '<key>: <value>', got {raw.strip()!r}")
        entries[-1][1].append((key, value.rstrip()))

    acknowledged: dict[Finding, str] = {}
    for number, pairs in entries:
        keys = [k for k, _ in pairs]
        if keys[0] != "area":
            raise SystemExit(f"{where}:{number}: an entry starts with 'area'")
        unknown = set(keys) - {"area", "reason", *SNAPSHOT_KEYS, *CONTRACT_KEYS}
        if unknown:
            raise SystemExit(f"{where}:{number}: unknown key(s) {', '.join(sorted(unknown))}")
        for once in ("area", "reason", "old", "rule", "at", "text"):
            if keys.count(once) > 1:
                raise SystemExit(f"{where}:{number}: '{once}' appears more than once")
        if "reason" not in keys:
            raise SystemExit(f"{where}:{number}: every entry needs a one-line 'reason'")
        values = dict(pairs)
        if "old" in keys:
            if "new" not in keys or set(keys) & set(CONTRACT_KEYS):
                raise SystemExit(f"{where}:{number}: a snapshot entry has 'old' and one or more 'new', nothing else")
            finding = snapshot_finding(values["area"], values["old"], [v for k, v in pairs if k == "new" and v != REMOVED])
        elif "rule" in keys and "at" in keys:
            if set(keys) & set(SNAPSHOT_KEYS):
                raise SystemExit(f"{where}:{number}: a contract entry has 'rule', 'at' and optionally 'text'")
            finding = contract_finding(values["area"], values["rule"], values["at"], values.get("text", ""))
        else:
            raise SystemExit(f"{where}:{number}: an entry has 'old' and 'new' (snapshot) or 'rule' and 'at' (contract)")
        if finding in acknowledged:
            raise SystemExit(f"{where}:{number}: duplicate entry")
        acknowledged[finding] = values["reason"]
    return acknowledged


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
    parser.add_argument(
        "--github-repo",
        metavar="OWNER/NAME",
        help="skip vX.Y.Z tags whose GitHub release is flagged pre-release or draft (token: GH_TOKEN)",
    )
    parser.add_argument("--root", default=".", help="repository root (default: the current directory)")
    args = parser.parse_args(argv)
    root = Path(git(Path(args.root), "rev-parse", "--show-toplevel").stdout.strip())

    # Validated on every run, so a malformed entry fails before a release exists.
    acks = load_acknowledgments(root / ACKNOWLEDGMENTS)
    skip = prerelease_tags(args.github_repo, os.environ.get("GH_TOKEN")) if args.github_repo else set()
    baseline = args.baseline or latest_stable_tag(root, args.fetch, skip)
    if baseline is not None and args.fetch:
        git(root, "fetch", "--quiet", "--no-tags", "--depth=1", args.fetch, f"+refs/tags/{baseline}:refs/tags/{baseline}")
    if baseline is None:
        print("no stable release yet (no vX.Y.Z tag with X >= 2): nothing to compare against.", file=out)
        return 0
    for what, rev in (("baseline", baseline), ("base", args.base)):
        if rev and git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", check=False).returncode != 0:
            raise SystemExit(f"{what} {rev} is not a commit in this repository (fetch it first)")
    if not any(files_at(root, baseline, d, ".api.txt") for d in SURFACE_DIRS):
        print(f"note: {baseline} carries no API surface snapshots; only contract/ is compared.", file=out)

    report = Report()
    head = findings(root, baseline, None, find_oasdiff, report)
    acknowledged = {f: reason for f, reason in acks.items() if f in head}
    stale = [f for f in acks if f not in head]
    head -= set(acknowledged)
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
    for finding in stale:
        print(f"  note: {ACKNOWLEDGMENTS} entry matches no finding (stale; it may be removed):", file=out)
        print(finding.render(), file=out)
    if acknowledged:
        print(f"{len(acknowledged)} change(s) acknowledged as compatible in {ACKNOWLEDGMENTS}:", file=out)
        for finding, reason in sorted(acknowledged.items()):
            print(f"{finding.render()}\n      reason: {reason}", file=out)
    if accepted:
        print(f"{len(accepted)} breaking change(s) since {baseline} were already on the base branch:", file=out)
        for finding in accepted:
            print(finding.render(), file=out)
    if not new:
        print(f"No breaking change {scope}.", file=out)
        return 0
    print(f"{len(new)} breaking change(s) {scope}:", file=out)
    for finding in new:
        print(finding.render(), file=out)
    if fragments:
        print(f"Announced by {', '.join(fragments)}: the next release is a major one (the maintainer decides).", file=out)
        return 0
    print(
        f"\nThese break code written against {baseline}. Restore compatibility, or announce the break "
        "with a changelog.d/<slug>.breaking.md fragment (changelog.d/README.md); the maintainer then "
        "decides the major version bump. If a change listed here is in fact compatible (the check is "
        f"conservative about changed signatures), copy its entry into {ACKNOWLEDGMENTS} and add a "
        "one-line 'reason:'.",
        file=out,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
