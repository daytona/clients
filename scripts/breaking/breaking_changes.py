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
  value, or a changed signature or type that is not a known-compatible change
  (an added optional parameter, an object type that gained optional members, a
  widened union, an added base class, a flag that gained a shorthand or turned
  persistent, hidden or deprecated, a usage that gained optional arguments);
- ``contract/*.json``: the OpenAPI contracts with oasdiff (``ERR`` and
  ``WARN`` levels, such as a removed request parameter; enums the contract
  tags ``x-daytona-open-enum`` may gain values) plus their embedded error
  catalogs, the standalone error catalogs (a changed source, a removed code,
  a changed status, a removed extra) and the other contract files (a removed
  field, or a changed value outside the fields listed as documentation).

A breaking change passes only when a ``changelog.d/*.breaking.md`` fragment
announces it; the maintainer then decides the major bump. A change the check
reports but that is in fact compatible goes into
``scripts/breaking/compatible-changes.txt`` instead, copied exactly as the
check prints it, with a one-line reason; it then passes in every mode.
With ``--base REV``
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
import urllib.error
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
INFORMATIONAL_KINDS = ("internal-constructor", "passed-in")
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
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                releases = json.loads(response.read())
        except (OSError, ValueError) as error:
            raise SystemExit(f"could not list the GitHub releases of {repo}: {error}") from None
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


def object_parts(text: str) -> list[str | list[str]]:
    """``text`` cut into plain chunks and the member lists of the object
    type literals in it (``{ a: T; b?: U; }``), at any depth."""
    parts: list[str | list[str]] = []
    start, i, quote = 0, 0, ""
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "'\"`":
            quote = ch
        elif ch == "{":
            end = matching_paren(text, i)
            if end < 0:
                break
            parts.append(text[start:i])
            parts.append(split_top(text[i + 1 : end], ";"))
            start = i = end + 1
            continue
        i += 1
    parts.append(text[start:])
    return parts


def member_key(member: str) -> tuple[str, str]:
    """``(name, type)`` of one object-literal member; the name keeps ``?``."""
    depth, quote = 0, ""
    for i, ch in enumerate(member):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"`":
            quote = ch
        elif ch in OPENS:
            depth += 1
        elif ch in CLOSES and not (ch == ">" and member[i - 1 : i] == "="):
            depth -= 1
        elif ch == ":" and depth == 0:
            return member[:i].strip(), member[i + 1 :].strip()
    return member.strip(), ""


# --- directions -----------------------------------------------------------------
#
# Every type position has a direction, and the check compares it by that:
# - IN: callers supply the value: a parameter of a function the SDK
#   implements, a member of a type callers only pass in, the return value of
#   a callback callers implement. Any widening is compatible, `undefined` /
#   `null` / `None` included; narrowing breaks.
# - OUT: the SDK supplies it and callers read it: the return value of a
#   function the SDK implements, a parameter of a callback callers implement.
#   Narrowing is compatible; widening only by literal (enum) values, the
#   documented growth; a new `undefined` / `null` / `None` breaks.
# - BOTH: either way, or not known: a type alias, a member of a type the SDK
#   returns (and may take back). Only literal values may be added.
# A function type takes its position's direction: in an IN position callers
# implement it, so its parameters are OUT and its return value IN; in an OUT
# position the SDK implements it; in a BOTH position it must stay the same.
IN, OUT, BOTH = "in", "out", "both"
LITERAL = re.compile(r"""Literal:.*|'.*'|".*"|-?\d+(?:\.\d+)?|true|false""")


def is_literal(member: str) -> bool:
    return LITERAL.fullmatch(member.strip()) is not None


def object_compatible(old: str, new: str, direction: str = BOTH) -> bool:
    """``new`` equals ``old`` except that object type literals in it gained
    optional members (members kept with the same name and optionality, and a
    type compatible in ``direction``)."""
    if old == new:
        return True
    old_parts, new_parts = object_parts(old), object_parts(new)
    if len(old_parts) != len(new_parts) or len(old_parts) == 1:
        return False
    for a, b in zip(old_parts, new_parts):
        if isinstance(a, str) or isinstance(b, str):
            if a != b:
                return False
            continue
        old_members = dict(member_key(m) for m in a)
        new_members = dict(member_key(m) for m in b)
        for name, kind in old_members.items():
            if name not in new_members or not type_compatible(kind, new_members[name], direction):
                return False
        if any(not name.endswith("?") for name in set(new_members) - set(old_members)):
            return False
    return True


NULLISH = frozenset({"undefined", "null", "None"})


def union_set(text: str) -> set[str]:
    """The members of a type's top-level union; ``Literal[...]`` values count
    one by one, so ``Literal['a'] | None`` and ``Literal['a', 'b'] | None``
    compare member by member."""
    members: set[str] = set()
    for member in split_top(text.strip(), "|"):
        literal = re.fullmatch(r"(?:typing\.)?Literal\[(.*)\]", member)
        if literal:
            # `Literal[None]` is `None`: an absent value, not a literal.
            members |= {"None" if v == "None" else f"Literal:{v}" for v in split_top(literal[1], ",")}
        else:
            members.add(member)
    return members


@dataclass(frozen=True)
class FunctionType:
    """A function type as printed: TypeScript ``<G>(a: T) => R`` (``form``
    ``ts``) or Python ``Callable[[A], R]`` (``py``, ``params`` None for
    ``Callable[..., R]``), with the nullish members of its union."""

    form: str
    generics: str
    params: tuple[str, ...] | None
    returns: str
    nullish: frozenset[str]


def arrow_type(body: str, nullish: frozenset[str]) -> FunctionType | None:
    """``<G>(a: T) => R``: an arrow type's return type runs to the end."""
    start, generics = 0, ""
    if body.startswith("<"):
        end = matching_paren(body, 0)
        if end < 0:
            return None
        generics, start = body[: end + 1], end + 1
    if body[start : start + 1] != "(":
        return None
    close = matching_paren(body, start)
    if close < 0 or not body[close + 1 :].startswith(" => "):
        return None
    return FunctionType("ts", generics, tuple(split_top(body[start + 1 : close], ",")), body[close + 5 :].strip(), nullish)


def function_type(text: str) -> FunctionType | None:
    text = text.strip()
    whole = arrow_type(text, frozenset())
    if whole is not None:
        return whole
    members = split_top(text, "|")
    nullish = frozenset(m for m in members if m in NULLISH)
    rest = [m for m in members if m not in NULLISH]
    if len(rest) != 1:
        return None
    body = rest[0].strip()
    while body.startswith("(") and matching_paren(body, 0) == len(body) - 1:
        body = body[1:-1].strip()
    callable_ = re.fullmatch(r"(?:typing\.|collections\.abc\.)?Callable\[(.*)\]", body)
    if callable_:
        parts = split_top(callable_[1], ",")
        if len(parts) != 2:
            return None
        args, ret = parts[0].strip(), parts[1].strip()
        if args == "...":
            return FunctionType("py", "", None, ret, nullish)
        if args.startswith("[") and args.endswith("]"):
            return FunctionType("py", "", tuple(split_top(args[1:-1], ",")), ret, nullish)
        return None
    return arrow_type(body, nullish)


def type_compatible(old: str, new: str, direction: str) -> bool:
    """Whether a type position may change from ``old`` to ``new`` (see the
    directions above). Only the top-level union is compared member by member:
    growth inside a type argument (``Omit<T, 'a' | 'b'>``) is not recognised,
    since there widening can remove properties."""
    if old == new:
        return True
    old_fn, new_fn = function_type(old), function_type(new)
    if old_fn or new_fn:
        return bool(old_fn and new_fn) and function_compatible(old_fn, new_fn, direction)
    if object_compatible(old, new, direction):
        return True
    old_members, new_members = union_set(old), union_set(new)
    added, removed = new_members - old_members, old_members - new_members
    if direction == IN:
        return not removed
    if direction == OUT and not added:
        return bool(new_members)
    # The documented growth: literal (enum) values, or members of a union
    # that already had several; never a new `undefined` / `null` / `None`.
    if removed or added & NULLISH:
        return False
    return all(is_literal(m) for m in added) or len(old_members - NULLISH) >= 2


def unwrap_promise(old: str, new: str) -> tuple[str, str]:
    po, pn = re.fullmatch(r"Promise<(.*)>", old), re.fullmatch(r"Promise<(.*)>", new)
    return (po[1], pn[1]) if po and pn else (old, new)


def function_compatible(old: FunctionType, new: FunctionType, direction: str) -> bool:
    if direction == BOTH or old.form != new.form or old.generics != new.generics:
        return False
    if not (old.nullish <= new.nullish if direction == IN else new.nullish <= old.nullish):
        return False
    implementer = "caller" if direction == IN else "sdk"
    if old.params is None or new.params is None:
        if old.params != new.params:
            return False
    elif not params_compatible(list(old.params), list(new.params), implementer, "callable" if old.form == "py" else "ts"):
        return False
    return type_compatible(*unwrap_promise(old.returns, new.returns), IN if implementer == "caller" else OUT)


def parse_param(text: str) -> tuple[str, str, bool, str, str]:
    """``(star, name, optional_marker, type, default)`` of one parameter as
    printed: TypeScript ``name?: T`` / ``...name: T``, Python
    ``name: T = default`` / ``*args: T`` / ``**kwargs: T``."""
    head, _, annotation_text = text.partition(":")
    star = re.match(r"\*\*|\*|\.\.\.", head)
    marker = star[0] if star else ""
    name = head[len(marker) :].strip()
    optional = name.endswith("?")
    name = name.removesuffix("?")
    parts = split_top(annotation_text, " = ") if annotation_text else []
    kind = parts[0].strip() if parts else ""
    default = " = ".join(parts[1:]) if len(parts) > 1 else ""
    if not annotation_text and " = " in head:
        name, _, default = head.partition(" = ")
        name = name.strip()
    return marker, name, optional, kind, default.strip()


def param_compatible(old: str, new: str, *, positional_names: bool = True) -> bool:
    """A caller passing ``old`` to a function the SDK implements still can:
    the same name (only where callers can use it; TypeScript has no keyword
    arguments), still optional if it was, the same default, and a type
    accepting at least as much (an IN position)."""
    if old == new:
        return True
    if old in ("*", "/") or new in ("*", "/"):
        return False
    o, n = parse_param(old), parse_param(new)
    if o[0] != n[0] or (positional_names and o[1] != n[1]):
        return False
    old_optional, new_optional = o[2] or bool(o[4]), n[2] or bool(n[4])
    if old_optional and not new_optional:
        return False
    if o[4] and o[4] != n[4]:
        return False
    return type_compatible(o[3], n[3], IN)


def supplied_param_compatible(old: str, new: str, *, names: bool) -> bool:
    """A parameter of a callback callers implement, which the SDK supplies:
    an OUT position, where ``name?: T`` counts as ``T | undefined``."""
    if old == new:
        return True
    o, n = parse_param(old), parse_param(new)
    if o[0] != n[0] or o[4] != n[4] or (names and o[1] != n[1]):
        return False
    old_type = f"{o[3]} | undefined" if o[2] else o[3]
    new_type = f"{n[3]} | undefined" if n[2] else n[3]
    return type_compatible(old_type, new_type, OUT)


def is_star(param: str) -> bool:
    return param == "*" or (param.startswith("*") and not param.startswith("**"))


def params_compatible(old_params: list[str], new_params: list[str], implementer: str, form: str) -> bool:
    """Parameter lists, by who implements the function. ``form`` is ``ts``,
    ``def`` (Python, named) or ``callable`` (Python ``Callable``, types
    only)."""
    if implementer == "both":
        return old_params == new_params
    if implementer == "caller":
        if form == "ts":
            # A callback may take fewer parameters than the SDK passes, so a
            # new trailing parameter is compatible; each kept one is OUT.
            return len(new_params) >= len(old_params) and all(
                supplied_param_compatible(o, n, names=False) for o, n in zip(old_params, new_params)
            )
        if form == "callable":
            return len(new_params) == len(old_params) and all(
                type_compatible(o, n, OUT) for o, n in zip(old_params, new_params)
            )
        return len(new_params) == len(old_params) and all(
            supplied_param_compatible(o, n, names=True) for o, n in zip(old_params, new_params)
        )
    if form == "callable":
        return len(new_params) == len(old_params) and all(type_compatible(o, n, IN) for o, n in zip(old_params, new_params))
    # Old parameters from here on are keyword-only (Python): an insertion
    # among them shifts nothing.
    keyword_from = next((i + 1 for i, p in enumerate(old_params) if is_star(p)), len(old_params))
    j = 0
    for param in new_params:
        if j < len(old_params) and param_compatible(old_params[j], param, positional_names=form != "ts"):
            j += 1
            continue
        if not is_optional_param(param) or j < min(keyword_from, len(old_params)):
            return False
    return j == len(old_params)


# The kind words of a signature line, before its type parameters.
SIGNATURE_KIND = re.compile(r"(?:[a-z-]+ )*(?:method|function|constructor|internal-constructor|call|new|def) ?")


def parameter_list_at(rest: str) -> int:
    """Where a signature line's parameter list opens, or -1 when the line is
    no signature: after its kind words and a balanced type-parameter list
    (``<T extends (x: number) => void>``, whose parentheses and ``=>`` are
    part of it)."""
    kind = SIGNATURE_KIND.match(rest)
    if not kind:
        return -1
    at = kind.end()
    if rest[at : at + 1] == "<":
        end = matching_paren(rest, at)
        if end < 0:
            return -1
        at = end + 1
    return at if rest[at : at + 1] == "(" else -1


def split_signature(rest: str) -> tuple[str, str, str] | None:
    """``(head, parameters, tail)`` of a signature line."""
    at = parameter_list_at(rest)
    if at < 0:
        return None
    close = matching_paren(rest, at)
    if close < 0:
        return None
    return rest[:at], rest[at + 1 : close], rest[close + 1 :].strip()


def compatible_call(old: str, new: str, *, typescript: bool = False, implementer: str = "sdk") -> bool:
    """A signature line (a method, function, constructor, call or construct
    signature, ``def``) that still accepts every call it did, judged by who
    implements it: the SDK (callers call it: parameters IN, return value
    OUT), callers (a method of a type they pass in: parameters OUT, return
    value IN), or both (unchanged)."""
    o, n = parameter_list_at(old), parameter_list_at(new)
    if o < 0 or n < 0 or old[:o] != new[:n]:
        return False
    oc, nc = matching_paren(old, o), matching_paren(new, n)
    if oc < 0 or nc < 0:
        return False
    if implementer == "both":
        return False
    old_tail, new_tail = old[oc + 1 :].strip(), new[nc + 1 :].strip()
    for prefix in (":", "->"):
        if old_tail.startswith(prefix) and new_tail.startswith(prefix):
            old_ret, new_ret = unwrap_promise(old_tail[len(prefix) :].strip(), new_tail[len(prefix) :].strip())
            if not type_compatible(old_ret, new_ret, IN if implementer == "caller" else OUT):
                return False
            break
    else:
        if old_tail != new_tail:
            return False
    old_params, new_params = split_top(old[o + 1 : oc], ","), split_top(new[n + 1 : nc], ",")
    return params_compatible(old_params, new_params, implementer, "ts" if typescript else "def")


def union_members(detail: str) -> list[str]:
    if not detail.startswith("= "):
        return []
    body = detail[2:].strip()
    literal = re.fullmatch(r"Literal\[(.*)\]", body)
    return split_top(literal[1], ",") if literal else split_top(body, "|")


# Python class lines list their bases in parentheses. Anchored to the class
# kinds, so a constructor or method line (`constructor (message?: string)`)
# never reads as a class gaining bases.
CLASS_LINE = re.compile(r"((?:class|dataclass|dataclass frozen|enum|protocol)(?: <[^()]*>)?) \((.*)\)")


FLAG_LINE = re.compile(r"((?:[a-z]+ )*)flag (\S+)((?: -\S+(?: \(deprecated\))?)?)( required)? default=(.*)")
COMMAND_LINE = re.compile(r"((?:[a-z]+ )*)command")
# Still accepted: a flag or command that turned hidden or deprecated works.
STILL_ACCEPTED = {"hidden", "deprecated"}


ARGS_PROBED = 16  # cli/cmd/surface_test.go probes 0..16 positional arguments
ARGS_UNBOUNDED = -1  # stands for "any number" in arg_counts()


def arg_counts(spec: str) -> set[int]:
    """The argument counts an `args` line accepts: `1`, `0-2`, `1+`, `0,2`."""
    counts: set[int] = set()
    for part in spec.split(","):
        if part.endswith("+"):
            # `N+` has no upper bound; a validator that stops past the probes
            # prints `N-16` instead, which lacks the unbounded marker.
            counts |= set(range(int(part[:-1]), ARGS_PROBED + 1)) | {ARGS_UNBOUNDED}
        elif "-" in part:
            low, high = part.split("-")
            counts |= set(range(int(low), int(high) + 1))
        elif part.isdigit():
            counts.add(int(part))
    return counts


def compatible_cli(old: str, new: str) -> bool:
    """A CLI command or flag line that still accepts every old invocation:
    hidden or deprecated now, a flag that gained a shorthand or became
    persistent, a usage that gained trailing optional arguments, or an
    argument validator that accepts more argument counts."""
    o, n = COMMAND_LINE.fullmatch(old), COMMAND_LINE.fullmatch(new)
    if o and n:
        return set(n[1].split()) - set(o[1].split()) <= STILL_ACCEPTED and set(o[1].split()) <= set(n[1].split()) | STILL_ACCEPTED
    o, n = FLAG_LINE.fullmatch(old), FLAG_LINE.fullmatch(new)
    if o and n:
        old_mods, new_mods = set(o[1].split()), set(n[1].split())
        if o[2] != n[2] or o[5] != n[5] or (n[4] and not o[4]):
            return False
        if "persistent" in old_mods and "persistent" not in new_mods:
            return False
        if new_mods - old_mods - STILL_ACCEPTED - {"persistent"}:
            return False
        old_short, new_short = o[3].strip(), n[3].strip()
        return not old_short or new_short in (old_short, f"{old_short} (deprecated)")
    if old.startswith("args ") and new.startswith("args "):
        return arg_counts(old[5:]) <= arg_counts(new[5:])
    if old.startswith("usage") and new.startswith(old):
        return re.fullmatch(r"(?: \[[^\[\]]*\](?:\.\.\.)?)+", new[len(old) :]) is not None
    return False


# A one-line union, Literal, tuple or set that grew past the one-line limit
# prints as a head line plus one line per member.
GROWN = (
    (re.compile(r"type = (.*)"), ("type = union", "type = Literal"), "union-member"),
    (re.compile(r"const (readonly )?\[(.*)\]"), ("const readonly tuple", "const tuple"), "tuple-element"),
    (re.compile(r"const (frozenset|set|tuple|list)\((.*)\)"), None, "element"),
)


def grew_into_lines(old: str, news: list[str]) -> bool:
    for pattern, heads, member_kind in GROWN:
        match = pattern.fullmatch(old)
        if not match:
            continue
        if member_kind == "union-member":
            members = union_members(f"= {match[1]}")
        else:
            members = split_top(match[2], ",")
        expected = heads or (f"const {match[1]}",)
        if not any(head in news for head in expected):
            continue
        listed = {n.split(" ", 1)[1] for n in news if n.startswith(f"{member_kind} ")}
        return bool(members) and set(members) <= listed and not (listed - set(members)) & NULLISH
    return False


def moved_to_persistent_ancestor(key: str, rests: list[str], new: dict[str, list[str]]) -> bool:
    """A CLI flag removed from a command but now a persistent flag of one of
    its parents: the command still accepts it."""
    match = re.fullmatch(r"(.+) --(\S+)", key)
    if not match:
        return False
    path, flag = match[1], match[2]
    old = FLAG_LINE.fullmatch(rests[0])
    while " " in path:
        path = path.rsplit(" ", 1)[0]
        for rest in new.get(f"{path} --{flag}", []):
            now = FLAG_LINE.fullmatch(rest)
            same = old and now and old[2] == now[2] and old[5] == now[5]
            # A parent flag that is required breaks calls that omitted it.
            if same and "persistent" in now[1].split() and (old[4] or not now[4]):
                return True
    return False


# A member typed by its rest: TypeScript `readonly optional property T`,
# Python `attribute T`, `readonly property T`, `optional key T`,
# `field kw_only T = default`.
MEMBER_LINE = re.compile(r"((?:[a-z]+ )*?(?:property|attribute|key|field(?: kw_only)?(?: init=False)?)) (.*)")
TUPLE_CONST = re.compile(r"const (readonly )?\[(.*)\]")
COLLECTION = re.compile(r"(frozenset|set|tuple|list)\((.*)\)")


def collection_grew(old: str, new: str) -> bool:
    """A one-line collection value that only gained elements: a set at any
    position, a tuple or list at the end (positions are part of its type)."""
    o, n = TUPLE_CONST.fullmatch(old), TUPLE_CONST.fullmatch(new)
    if o and n:
        old_items, new_items = split_top(o[2], ","), split_top(n[2], ",")
        return o[1] == n[1] and new_items[: len(old_items)] == old_items
    o, n = COLLECTION.fullmatch(old), COLLECTION.fullmatch(new)
    if not (o and n and o[1] == n[1]):
        return False
    old_items, new_items = split_top(o[2], ","), split_top(n[2], ",")
    if o[1] in ("frozenset", "set"):
        return set(old_items) <= set(new_items)
    return new_items[: len(old_items)] == old_items


def item_grew(old: str, new: str) -> bool:
    """A dict ``item`` line whose key is unchanged and whose value is a
    collection that only gained elements."""
    old_key, sep, old_value = old.removeprefix("item ").partition(": ")
    new_key, _, new_value = new.removeprefix("item ").partition(": ")
    return bool(sep) and old.startswith("item ") and old_key == new_key and collection_grew(old_value, new_value)


TYPE_ALIAS_LINE = re.compile(r"type ((?:<.*> )?)= (.*)")


def compatible_change(
    old: str, new: str, *, typescript: bool = False, direction: str = BOTH, implementer: str = "sdk"
) -> bool:
    """A known-compatible change of one line's rest (kind + signature).
    ``direction`` is that of the line's type when it types a member (see the
    directions above); ``implementer`` says who implements it when it is a
    signature: ``sdk``, ``caller`` or ``both``."""
    if compatible_call(old, new, typescript=typescript, implementer=implementer):
        return True
    if compatible_cli(old, new):
        return True
    old_kind, new_kind = old.split(" ", 1)[0], new.split(" ", 1)[0]
    # A type alias: its direction is not known, so BOTH.
    o, n = TYPE_ALIAS_LINE.fullmatch(old), TYPE_ALIAS_LINE.fullmatch(new)
    if o and n:
        return o[1] == n[1] and type_compatible(o[2], n[2], BOTH)
    o, n = MEMBER_LINE.fullmatch(old), MEMBER_LINE.fullmatch(new)
    # A member the SDK supplies that is now always present: narrower.
    if o and n and direction == OUT and o[1].split(" ").count("optional") == 1:
        if n[1] == " ".join(w for w in o[1].split(" ") if w != "optional"):
            o_parts, n_parts = split_top(o[2], " = "), split_top(n[2], " = ")
            if o_parts[1:] == n_parts[1:] and type_compatible(o_parts[0], n_parts[0], OUT):
                return True
    if o and n and o[1] == n[1]:
        old_parts, new_parts = split_top(o[2], " = "), split_top(n[2], " = ")
        if old_parts[1:] == new_parts[1:] and type_compatible(old_parts[0], new_parts[0], direction):
            return True
        # A dataclass field that gained a default (its constructor parameter
        # became optional; the attribute reads the same).
        if o[1] in ("field", "field kw_only") and len(old_parts) == 1 and len(new_parts) == 2 and old_parts[0] == new_parts[0]:
            return True
    # A one-line constant collection that only gained elements.
    if old_kind == new_kind == "const" and collection_grew(old[6:], new[6:]):
        return True
    if old_kind == new_kind == "const" and old.startswith("const readonly [") and collection_grew(old, new):
        return True
    # A Python class that only gained base classes.
    o, n = CLASS_LINE.fullmatch(old), CLASS_LINE.fullmatch(new)
    return bool(o and n and o[1] == n[1] and set(split_top(o[2], ",")) <= set(split_top(n[2], ",")))


def made_optional(old: str, new: str, *, typescript: bool = False) -> bool:
    """A member that became optional: TypeScript ``property`` / ``method``
    gaining ``optional``, a TypedDict ``key`` becoming ``optional key``,
    possibly with a compatible change of its type too. Compatible for a type
    callers only pass in (an IN position)."""
    for kind in ("property ", "method ", "key "):
        at = old.find(kind)
        if at < 0 or (at > 0 and old[at - 1] != " ") or "optional " in old[:at]:
            continue
        marker = f"{old[:at]}optional {kind}"
        if not new.startswith(marker):
            continue
        required_again = old[:at] + new[len(old[:at]) + len("optional ") :]
        return required_again == old or compatible_change(
            old, required_again, typescript=typescript, direction=IN, implementer="caller"
        )
    return False


def gained_short_alias(old: str, new: str) -> bool:
    """A migration-tool flag line that only gained short aliases, the
    existing ones kept: ``flag switch`` -> ``flag switch -y``,
    ``flag switch -h`` -> ``flag switch -h -?``."""
    return old.startswith("flag ") and re.fullmatch(re.escape(old) + r"(?: -\S+)+", new) is not None


def group_line(rest: str) -> tuple[str, frozenset[str]] | None:
    parts = rest.split(" ")
    if len(parts) < 3 or parts[0] != "flag-group":
        return None
    return parts[1], frozenset(parts[2:])


def group_loosened(old: tuple[str, frozenset[str]], new: tuple[str, frozenset[str]]) -> bool:
    """``new`` accepts every invocation ``old`` did: fewer flags in a
    mutually-exclusive or required-together group, more in a one-required
    group."""
    if old[0] != new[0]:
        return False
    if old[0] == "one-required":
        return old[1] <= new[1]
    return new[1] <= old[1]


def informational(rest: str) -> bool:
    return any(word in INFORMATIONAL_KINDS for word in rest.split(" ")[:3])


def is_child(key: str, parents: Iterable[str]) -> bool:
    return any(key.startswith(p + sep) for p in parents for sep in (".", "[", " "))


def group_of(entries: dict[str, list[str]], key: str) -> list[tuple[str, str]]:
    """``key``'s lines and its members', relative to ``key``."""
    return sorted(
        (k[len(key) :], rest)
        for k, rests in entries.items()
        if k == key or is_child(k, [key])
        for rest in rests
    )


def moved_key(key: str, old: dict[str, list[str]], new: dict[str, list[str]]) -> str | None:
    """A removed ``~Name@module`` key whose declaration reappears unchanged
    under another key for the same name (the file moved, or a same-named
    type took the plain key): a move, not a removal."""
    if not key.startswith("~"):
        return None
    base = key.split("@", 1)[0]
    lines = group_of(old, key)
    for candidate in new:
        if candidate in old or candidate.split("@", 1)[0] != base or candidate == key:
            continue
        if "." in candidate.removeprefix(base).split("@", 1)[-1].rsplit("/", 1)[-1]:
            continue  # a member key, not a declaration
        if group_of(new, candidate) == lines:
            return candidate
    return None


# Declarations whose members callers supply when they pass one in: a new
# required member breaks those callers. A dataclass's new required field
# shows in its constructor line instead.
SUPPLIED_HEADS = ("interface", "type = {...}", "typeddict", "protocol")
# The direction markers the generators print under a declaration.
PASS_ONLY_MARKER = "passed-in"
TWO_WAY_MARKERS = ("passed-in also-returned", "returned also-passed-in")


def marker_of(rests: Iterable[str]) -> str | None:
    """``pass-only``, ``two-way`` or None, from a declaration's lines."""
    rests = list(rests)
    if any(r in TWO_WAY_MARKERS for r in rests):
        return "two-way"
    if PASS_ONLY_MARKER in rests:
        return "pass-only"
    return None


def required_member(head: str, rest: str) -> bool:
    words = rest.split(" ")
    kinds = words[: next((i for i, w in enumerate(words) if w in ("property", "method", "key", "def", "async", "attribute")), len(words)) + 1]
    if head.startswith("typeddict"):
        return kinds[-1] == "key" and "optional" not in kinds
    if head.startswith("protocol"):
        return kinds[-1] in ("def", "async", "property", "attribute")
    return kinds[-1] in ("property", "method") and "optional" not in kinds


def new_required_members(name: str, old: dict[str, list[str]], new: dict[str, list[str]], report: Report) -> None:
    """A member added to a type callers pass in (its ``passed-in`` line), when
    callers must now supply it."""
    supplied = {
        key: next(r for r in rests if r.startswith(SUPPLIED_HEADS))
        for key, rests in new.items()
        if marker_of(rests) and key in old and any(r.startswith(SUPPLIED_HEADS) for r in rests)
    }
    for key in sorted(set(new) - set(old)):
        parent = next((p for p in supplied if key.startswith(p) and key[len(p) : len(p) + 1] in ".(["), None)
        if parent is None:
            continue
        suffix = key[len(parent) :]
        marker = next(r for r in new[parent] if r == PASS_ONLY_MARKER or r in TWO_WAY_MARKERS)
        for rest in new[key]:
            # A new call or construct signature (`X()`) is one more thing every
            # implementation must accept, and a new index signature (`X[]`)
            # one more constraint on every member callers' types have; a new
            # member must be supplied when it is required.
            member = (suffix[0] == "." and "." not in suffix[1:]) or (suffix.startswith("[") and suffix != "[]")
            if suffix in ("()", "[]") or (member and required_member(supplied[parent], rest)):
                report.breaking.append(snapshot_finding(name, f"{parent}: {marker}", [f"{key}: {rest}"]))


def compare_surface(name: str, old_text: str, new_text: str | None, report: Report) -> None:
    old = parse_surface(old_text)
    new = parse_surface(new_text or "")
    if new_text is None:
        report.breaking.append(snapshot_finding(name, "(the whole snapshot)"))
        return
    typescript = name.startswith("sdk-typescript/")
    # The migration tool's promise lets a minor release add flags, fields and
    # values, never change one: no widening there.
    allow_widening = not name.startswith("upgrade/")
    # Types callers only pass in (never returned): a member may become optional.
    pass_only = {k for k, rests in new.items() if marker_of(rests) == "pass-only" and marker_of(old.get(k, [])) == "pass-only"}
    # Types that go both ways (at the release or now).
    two_way = {k for entries in (old, new) for k, rests in entries.items() if marker_of(rests) == "two-way"}
    removed_keys: list[str] = []
    for key in sorted(old):
        news = new.get(key)
        # New lines that stand in for an old line accepted as compatible.
        consumed: set[str] = set()
        # The new line each old line survives as (itself, or a compatible
        # change of it), for the overload order below.
        survives_as: dict[str, str] = {}
        if news is None:
            if is_child(key, removed_keys):
                continue
            removed_keys.append(key)
            moved = moved_key(key, old, new)
            if moved is not None:
                report.notices.append(f"{name}: {key} moved to {moved} unchanged")
            elif all(informational(r) for r in old[key]):
                report.notices.append(f"{name}: removed {key}: {old[key][0]}")
            elif moved_to_persistent_ancestor(key, old[key], new):
                report.notices.append(f"{name}: {key} now inherited from a parent's persistent flag")
            else:
                report.breaking.append(snapshot_finding(name, f"{key}: {old[key][0]}"))
            continue
        for rest in old[key]:
            if rest in news:
                continue
            line = f"{key}: {rest}"
            # The key's new lines. A signature or member line with none added
            # names the lines the key still has (it was not removed); a set
            # member (union member, value, element) is itself removed.
            added = [f"{key}: {r}" for r in news if r not in old[key]]
            if not added and rest.split(" ", 1)[0] not in SET_KINDS:
                added = [f"{key}: {r}" for r in news]
            here = position(key, rest, old, pass_only, two_way)
            if informational(rest):
                report.notices.append(f"{name}: changed (not public API) {line}")
            elif rest.startswith("flag-group "):
                report.notices.append(f"{name}: flag group dropped or loosened (compatible) {line}")
            elif not allow_widening:
                # Only a flag gaining a short alias, as on the CLI surface.
                if any(gained_short_alias(rest, candidate) for candidate in news):
                    report.notices.append(f"{name}: compatible change of {line}")
                else:
                    report.breaking.append(snapshot_finding(name, line, added))
            elif rest.startswith("item ") and any(item_grew(rest, candidate) for candidate in news):
                report.notices.append(f"{name}: compatible change of {line}")
            elif rest.split(" ", 1)[0] in SET_KINDS:
                report.breaking.append(snapshot_finding(name, line, added))
            elif grew_into_lines(rest, news):
                report.notices.append(f"{name}: compatible change of {line}")
            elif (
                match := next(
                    (
                        c
                        for c in news
                        # Only a new line stands in for an old one, and only
                        # for one: a dropped overload is never credited to a
                        # sibling (which overload its calls now reach is not
                        # modelled; acknowledge it when a later one takes
                        # them all).
                        if c not in old[key]
                        and c not in consumed
                        and compatible_change(rest, c, typescript=typescript, **here)
                    ),
                    None,
                )
            ) is not None:
                consumed.add(match)
                survives_as[rest] = match
                report.notices.append(f"{name}: compatible change of {line}")
            elif parent_of(key, pass_only) and (
                match := next((c for c in news if made_optional(rest, c, typescript=typescript)), None)
            ) is not None:
                consumed.add(match)
                survives_as[rest] = match
                report.notices.append(f"{name}: member of a passed-in type made optional {line}")
            else:
                report.breaking.append(snapshot_finding(name, line, added))
        reported = any(f.area == name and dict(f.fields).get("old", "").startswith(f"{key}: ") for f in report.breaking)
        for rest in news:
            if rest in old[key]:
                continue
            # A new or tightened flag group restricts the invocations a
            # command accepts; a group that only loosened does not.
            group = group_line(rest)
            if group is not None:
                olds = [g for g in map(group_line, old[key]) if g is not None]
                if not any(group_loosened(o, group) for o in olds):
                    anchor = next((r for r in old[key] if COMMAND_LINE.fullmatch(r)), old[key][0])
                    report.breaking.append(snapshot_finding(name, f"{key}: {anchor}", [f"{key}: {rest}"]))
            # A nullish member joining a long union alias (see compatible_change).
            elif not reported and rest.split(" ", 1)[0] == "union-member" and rest.split(" ", 1)[1] in NULLISH:
                report.breaking.append(snapshot_finding(name, f"{key}: {old[key][0]}", [f"{key}: {rest}"]))
            # A new overload of a signature callers implement: every
            # implementation must now accept it too.
            elif (
                not reported
                and rest not in consumed
                and is_signature(rest)
                and position(key, rest, new, pass_only, two_way)["implementer"] in ("caller", "both")
            ):
                anchor = next((r for r in old[key] if is_signature(r)), old[key][0])
                report.breaking.append(snapshot_finding(name, f"{key}: {anchor}", [f"{key}: {rest}"]))
        overload_order(name, key, old[key], news, survives_as, pass_only, two_way, old, report, typescript)
    new_required_members(name, old, new, report)
    new_required_flags(name, old, new, report)


MEMBER_KINDS = ("property", "attribute", "key", "field")


def is_signature(rest: str) -> bool:
    return parameter_list_at(rest) > 0


def owner_of(key: str, entries: dict[str, list[str]]) -> str | None:
    """The declaration a member key belongs to: ``X`` for ``X.m``,
    ``X[Symbol.y]`` and ``X()``."""
    best = None
    for i, ch in enumerate(key):
        if ch in ".[(" and key[:i] in entries:
            best = key[:i]
    return best


def position(key: str, rest: str, entries: dict[str, list[str]], pass_only: set[str], two_way: set[str]) -> dict[str, str]:
    """The direction of a member line's type and who implements a signature
    line, from the declaration it belongs to (see the directions above).
    What cannot be classified compares in both directions (exact, except
    the documented growth)."""
    owner = owner_of(key, entries)
    if owner is None:
        # Module level: an SDK function, a constant, an alias.
        return {"direction": BOTH, "implementer": "sdk"}
    if owner in two_way:
        return {"direction": BOTH, "implementer": "both"}
    if owner in pass_only:
        return {"direction": IN, "implementer": "caller"}
    head = entries[owner][0]
    words = rest.split(" ")
    kind_at = next((i for i, w in enumerate(words) if w in MEMBER_KINDS), None)
    modifiers = set(words[:kind_at]) if kind_at is not None else set()
    is_class = head.startswith(("class", "abstract class", "dataclass", "enum"))
    if kind_at is None:
        # A method or constructor of a class, or of a type the SDK hands out.
        return {"direction": BOTH, "implementer": "sdk"}
    kind = words[kind_at]
    if kind == "field":
        # Callers build a dataclass whose constructor is public, and read it.
        init = entries.get(f"{owner}.__init__", [])
        built = any(r.startswith("constructor ") for r in init)
        return {"direction": BOTH if built else OUT, "implementer": "sdk"}
    if "readonly" in modifiers or (head.startswith(("interface", "type ")) and kind == "property"):
        return {"direction": OUT, "implementer": "sdk"}
    if is_class and kind == "property" and "readonly" not in modifiers:
        # A settable property: callers write it too.
        return {"direction": BOTH, "implementer": "sdk"}
    return {"direction": BOTH, "implementer": "sdk"}


def overload_order(
    name: str,
    key: str,
    olds: list[str],
    news: list[str],
    survives_as: dict[str, str],
    pass_only: set[str],
    two_way: set[str],
    entries: dict[str, list[str]],
    report: Report,
    typescript: bool = False,
) -> None:
    """The overloads of a function callers call (the SDK implements it, or
    it belongs to a type that goes both ways), in declaration order (the
    snapshot keeps it). A call resolves against the first overload it
    matches, so:
    - the surviving old overloads keep their order, and a new one comes
      after all of them (placed earlier, it can capture calls an old one
      answered);
    - an overload with a later one after it may only narrow its return
      type: widening or adding parameters (optional ones included) can
      capture calls a later overload answered. The last overload follows
      the normal rules.
    - no overload is dropped: which overload its calls reach now is not
      modelled, so a drop is always reported (acknowledge it when a later
      overload takes all its calls).
    An earlier overload's parameters compare by meaning, not text: a
    TypeScript positional name is not part of a call.
    Stops at the first finding for the key: a second, independent break
    under the same key is not listed (the exit status is the same)."""
    old_sig = [r for r in olds if is_signature(r)]
    new_sig = [r for r in news if is_signature(r)]
    if not old_sig:
        return
    if any(f.area == name and dict(f.fields).get("old", "").startswith(f"{key}: ") for f in report.breaking):
        return
    if position(key, old_sig[0], entries, pass_only, two_way)["implementer"] not in ("sdk", "both"):
        return
    survivors = [r if r in news else survives_as.get(r) for r in old_sig]
    for old_line, now in zip(old_sig[:-1], survivors[:-1]):
        if now is None or now == old_line:
            continue
        o, n = split_signature(old_line), split_signature(now)
        if o is None or n is None or o[0] != n[0] or not params_equivalent(o[1], n[1], typescript=typescript):
            report.breaking.append(snapshot_finding(name, f"{key}: {old_line}", [f"{key}: {now}"]))
            return
        old_ret, new_ret = o[2], n[2]
        for prefix in (":", "->"):
            if old_ret.startswith(prefix) and new_ret.startswith(prefix):
                old_ret, new_ret = unwrap_promise(old_ret[len(prefix) :].strip(), new_ret[len(prefix) :].strip())
                break
        if not type_compatible(old_ret, new_ret, OUT):
            report.breaking.append(snapshot_finding(name, f"{key}: {old_line}", [f"{key}: {now}"]))
            return
    if len(new_sig) < 2:
        return
    positions = [new_sig.index(c) for c in survivors if c is not None and c in new_sig]
    taken = {new_sig[i] for i in positions}
    added = [i for i, line in enumerate(new_sig) if line not in taken]
    if positions != sorted(positions) or (positions and any(i < max(positions) for i in added)):
        report.breaking.append(snapshot_finding(name, f"{key}: {old_sig[0]}", [f"{key}: {r}" for r in new_sig]))


def params_equivalent(old: str, new: str, *, typescript: bool) -> bool:
    """Two parameter lists that accept exactly the same calls: the same
    markers, defaults and optionality, the same types (an optional
    parameter's explicit ``| undefined`` aside), and the same names where
    callers can use them (Python; TypeScript has no keyword arguments)."""
    old_params, new_params = split_top(old, ","), split_top(new, ",")
    if len(old_params) != len(new_params):
        return False
    for a, b in zip(old_params, new_params):
        if a in ("*", "/") or b in ("*", "/"):
            if a != b:
                return False
            continue
        o, n = parse_param(a), parse_param(b)
        if o[0] != n[0] or o[2] != n[2] or o[4] != n[4] or (not typescript and o[1] != n[1]):
            return False
        old_types, new_types = union_set(o[3]), union_set(n[3])
        if o[2]:
            old_types.discard("undefined")
            new_types.discard("undefined")
        if old_types != new_types:
            return False
    return True


def parent_of(key: str, parents: set[str]) -> str | None:
    return next((p for p in parents if key.startswith(p) and key[len(p) : len(p) + 1] == "." and "." not in key[len(p) + 1 :]), None)


def new_required_flags(name: str, old: dict[str, list[str]], new: dict[str, list[str]], report: Report) -> None:
    """A required flag added to a command that existed at the release, or a
    required persistent flag that such a command now inherits: every earlier
    invocation of it now fails."""
    commands = {k for k, rests in old.items() if any(COMMAND_LINE.fullmatch(r) for r in rests)}
    for key in sorted(set(new) - set(old)):
        match = re.fullmatch(r"(.+) --(\S+)", key)
        if not match:
            continue
        path = match[1]
        for rest in new[key]:
            flag = FLAG_LINE.fullmatch(rest)
            if not flag or not flag[4]:
                continue
            persistent = "persistent" in flag[1].split()
            affected = path in commands or (persistent and any(c.startswith(path + " ") for c in commands))
            if affected:
                anchor = f"{path}: command" if path in commands else f"{path} (inherited by existing subcommands)"
                report.breaking.append(snapshot_finding(name, anchor, [f"{key}: {rest}"]))


# --- contracts ----------------------------------------------------------------


def open_enum_paths(node: object, at: tuple[object, ...] = ()) -> set[tuple[object, ...]]:
    """Where in a contract document an enum is tagged x-daytona-open-enum."""
    found: set[tuple[object, ...]] = set()
    if isinstance(node, dict):
        if "enum" in node and node.get("x-daytona-open-enum"):
            found.add(at)
        for k, v in node.items():
            found |= open_enum_paths(v, (*at, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found |= open_enum_paths(v, (*at, i))
    return found


def open_enums_extensible(
    node: object, only: set[tuple[object, ...]] | None = None, at: tuple[object, ...] = ()
) -> object:
    """Enums tagged x-daytona-open-enum in the x-extensible-enum form, which
    oasdiff lets gain values; with ``only``, just the enums at those paths.
    Used only to learn which response-side value additions the open tag
    permits (see ``oasdiff_findings``)."""
    if isinstance(node, dict):
        out = {k: open_enums_extensible(v, only, (*at, k)) for k, v in node.items()}
        if "enum" in out and out.get("x-daytona-open-enum") and (only is None or at in only):
            out["x-extensible-enum"] = out.pop("enum")
        return out
    if isinstance(node, list):
        return [open_enums_extensible(v, only, (*at, i)) for i, v in enumerate(node)]
    return node


def run_oasdiff(oasdiff: str, path: str, old_doc: object, new_doc: object) -> list[dict[str, object]]:
    with tempfile.TemporaryDirectory() as tmp:
        base, rev = Path(tmp, "base.json"), Path(tmp, "revision.json")
        base.write_text(json.dumps(old_doc))
        rev.write_text(json.dumps(new_doc))
        result = subprocess.run(
            [oasdiff, "breaking", str(base), str(rev), "--format", "json", "--allow-external-refs=false"],
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        raise SystemExit(f"oasdiff failed on {path}: {result.stderr.strip()}")
    items: list[dict[str, object]] = json.loads(result.stdout or "[]")
    return items


def oasdiff_key(item: dict[str, object]) -> tuple[str, str, str, str]:
    return (str(item.get("id")), str(item.get("operation")), str(item.get("path")), " ".join(str(item.get("text")).split()))


def is_response_value_added(item: dict[str, object]) -> bool:
    rule = str(item.get("id"))
    return rule.startswith("response-") and rule.endswith("enum-value-added")


def oasdiff_findings(oasdiff: str, path: str, old_text: str, new_text: str) -> tuple[set[Finding], set[str]]:
    """oasdiff on the contracts as they are (failing closed), minus the one
    change the open-enum tag permits: a value added to an enum tagged
    x-daytona-open-enum, at the same place, in both revisions, on the
    response side. Which response additions those are is learnt from a
    second run with those enums in the x-extensible-enum form; every request-side removal stays, also where oasdiff's
    x-extensible-enum checks do not reach (array-valued query parameters)."""
    old_doc, new_doc = json.loads(old_text), json.loads(new_text)
    raw = run_oasdiff(oasdiff, path, old_doc, new_doc)
    permitted: set[tuple[str, str, str, str]] = set()
    if any(is_response_value_added(item) for item in raw):
        # Only an enum open at the release and now (at the same place) may
        # grow: adding or dropping the tag in the same change fails closed.
        both = open_enum_paths(old_doc) & open_enum_paths(new_doc)
        still = {
            oasdiff_key(i)
            for i in run_oasdiff(oasdiff, path, open_enums_extensible(old_doc, both), open_enums_extensible(new_doc, both))
        }
        permitted = {oasdiff_key(i) for i in raw if is_response_value_added(i) and oasdiff_key(i) not in still}
    errors: set[Finding] = set()
    warnings: set[str] = set()
    for item in raw:
        if oasdiff_key(item) in permitted:
            continue
        where = " ".join(str(p) for p in (item.get("operation"), item.get("path")) if p) or "-"
        # WARN (2) counts too: it covers removed request parameters and
        # properties, which an older SDK may still send.
        level = item.get("level", 0)
        if isinstance(level, int) and level >= 2:
            errors.add(contract_finding(path, str(item.get("id")), where, " ".join(str(item.get("text")).split())))
        else:
            warnings.add(f"{path}: [{item.get('id')}] {where}: {item.get('text')}")
    return errors, warnings


EMBEDDED_CATALOGS = ("x-daytona-error-codes", "x-daytona-generic-codes")


def extension_findings(path: str, old_text: str, new_text: str) -> set[Finding]:
    """The other top-level ``x-daytona-*`` extensions of an OpenAPI contract
    (vocabularies such as ``x-daytona-log-channels``): a removed extension or
    a removed entry (a key of an object, an item of a list) breaks; added
    entries and changed descriptions do not."""
    old_doc, new_doc = json.loads(old_text), json.loads(new_text)
    found: set[Finding] = set()
    for extension, old in old_doc.items():
        if not extension.startswith("x-daytona-") or extension in EMBEDDED_CATALOGS:
            continue
        if extension not in new_doc:
            found.add(contract_finding(path, "extension-removed", extension))
            continue
        new = new_doc[extension]
        if isinstance(old, dict) and isinstance(new, dict):
            gone = [str(k) for k in old if k not in new]
        elif isinstance(old, list) and isinstance(new, list):
            kept = {json.dumps(v, sort_keys=True) for v in new}
            gone = [json.dumps(v, sort_keys=True) for v in old if json.dumps(v, sort_keys=True) not in kept]
        else:
            gone = [] if old == new else [json.dumps(old, sort_keys=True)]
        for entry in gone:
            found.add(contract_finding(path, "extension-entry-removed", f"{extension}:{entry}"))
    return found


def embedded_catalog_findings(path: str, old_text: str, new_text: str) -> set[Finding]:
    """The error catalogs an OpenAPI contract carries as extensions, which
    oasdiff does not compare: a removed code, a changed status or source, a
    removed extra."""
    old_doc, new_doc = json.loads(old_text), json.loads(new_text)
    found: set[Finding] = set()
    for extension in EMBEDDED_CATALOGS:
        old, new = old_doc.get(extension) or {}, new_doc.get(extension) or {}
        for code, entry in old.items():
            at = f"{extension}:{code}"
            if code not in new:
                found.add(contract_finding(path, "code-removed", at))
                continue
            for field_name, rule in (("status", "status-changed"), ("source", "source-changed")):
                if entry.get(field_name) != new[code].get(field_name):
                    change = f"{json.dumps(entry.get(field_name))} -> {json.dumps(new[code].get(field_name))}"
                    found.add(contract_finding(path, rule, at, change))
            for extra in set(map(str, entry.get("extras") or [])) - set(map(str, new[code].get("extras") or [])):
                found.add(contract_finding(path, "extra-removed", at, extra))
    return found


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
            found |= errors | embedded_catalog_findings(path, old_text, new_text)
            found |= extension_findings(path, old_text, new_text)
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


def fetch_tag(root: Path, remote: str, tag: str) -> None:
    """Fetch ``tag`` from ``remote`` unless its commit is already here. Only a
    shallow clone (CI) fetches shallowly; a full clone stays full."""
    if git(root, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}", check=False).returncode == 0:
        return
    shallow = git(root, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    depth = ["--depth=1"] if shallow else []
    git(root, "fetch", "--quiet", "--no-tags", *depth, remote, f"+refs/tags/{tag}:refs/tags/{tag}")


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
    if not (args.baseline or args.fetch or git(root, "tag", "--list", "v*").stdout.strip()):
        raise SystemExit("no release tags in this clone: fetch them (git fetch --tags) or pass --fetch origin")
    baseline = args.baseline or latest_stable_tag(root, args.fetch, skip)
    if baseline is not None and args.fetch:
        fetch_tag(root, args.fetch, baseline)
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
