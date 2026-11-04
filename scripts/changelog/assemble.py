#!/usr/bin/env python3
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Assemble per-PR changelog fragments into a Keep-a-Changelog file.

Each PR adds one file under ``changelog.d/`` named
``<id>.<type>.md`` holding the markdown for ONE bullet (no leading ``- ``, no
indentation).  At release time ``assemble`` folds the fragments into
``CHANGELOG.md`` under a new ``## [X.Y.Z] — DATE`` heading and
deletes them.  ``check`` validates fragment names and content (CI gate) and
``render`` prints the assembled section body without touching anything.

The fragment contract is documented in ``changelog.d/README.md``.

Python 3.11+, standard library only.
"""

from __future__ import annotations

import argparse
import os
import dataclasses
import datetime as _dt
import re
import sys
import tempfile
from pathlib import Path
from typing import Sequence

# Section order when assembling; only sections with fragments are emitted.
TYPES: tuple[tuple[str, str], ...] = (
    ("breaking", "BREAKING"),
    ("added", "Added"),
    ("changed", "Changed"),
    ("deprecated", "Deprecated"),
    ("removed", "Removed"),
    ("fixed", "Fixed"),
    ("security", "Security"),
)
TYPE_ORDER: dict[str, int] = {t: i for i, (t, _) in enumerate(TYPES)}
TYPE_HEADING: dict[str, str] = dict(TYPES)

# ``<id>`` is a PR number, optionally followed by ``-slug`` (several fragments
# of one type from one PR), or a plain slug when the PR number is not yet known.
ID_RE = re.compile(r"^[0-9]+(-[a-z0-9][a-z0-9-]*)?$|^[a-z0-9][a-z0-9-]*$")
PR_PREFIX_RE = re.compile(r"^([0-9]+)(?:-|$)")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z][0-9A-Za-z.-]*)?$")
HEADING_RE = re.compile(r"^## \[")

IGNORED_FILES = frozenset({"README.md"})


class FragmentError(Exception):
    """One or more fragments are invalid; ``problems`` lists them."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = list(problems)


@dataclasses.dataclass(frozen=True)
class Fragment:
    path: Path
    id: str
    type: str
    lines: tuple[str, ...]  # trimmed, non-empty

    @property
    def pr(self) -> int | None:
        """The PR number the id starts with, or ``None`` for slug-only ids."""
        m = PR_PREFIX_RE.match(self.id)
        return int(m.group(1)) if m else None

    @property
    def sort_key(self) -> tuple[int, int, str]:
        pr = self.pr
        return (0, pr, self.id) if pr is not None else (1, 0, self.id)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def parse_name(name: str) -> tuple[str, str] | str:
    """Return ``(id, type)`` for a valid fragment file name, or an error string."""
    parts = name.split(".")
    if len(parts) != 3 or parts[2] != "md":
        return "file name must be <id>.<type>.md"
    frag_id, frag_type, _ = parts
    if frag_type not in TYPE_ORDER:
        return (
            f"unknown type {frag_type!r}; expected one of "
            + ", ".join(t for t, _ in TYPES)
        )
    if not ID_RE.match(frag_id):
        return (
            f"invalid id {frag_id!r}; expected a PR number, optionally followed by "
            "-slug (e.g. 1234 or 1234-watch-exit), or a plain lowercase slug"
        )
    return frag_id, frag_type


def trim_content(text: str) -> tuple[str, ...]:
    """Split fragment text into lines with leading/trailing blank lines removed."""
    # Split on \n / \r\n only; a bare \r or NEL is content.
    lines = text.replace("\r\n", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return tuple(lines)


def load_dir(directory: Path) -> list[Fragment]:
    """Load every fragment in ``directory``; raise FragmentError on any problem."""
    problems: list[str] = []
    fragments: list[Fragment] = []
    if not directory.is_dir():
        raise FragmentError([f"{directory}: not a directory"])
    for path in sorted(directory.iterdir()):
        if path.name in IGNORED_FILES:
            continue
        if not path.is_file():
            problems.append(f"{path}: not a regular file")
            continue
        parsed = parse_name(path.name)
        if isinstance(parsed, str):
            problems.append(f"{path}: {parsed}")
            continue
        frag_id, frag_type = parsed
        try:
            text = path.read_text(encoding="utf-8-sig")  # -sig: drop a leading BOM
        except UnicodeDecodeError:
            problems.append(f"{path}: not valid UTF-8")
            continue
        if "\x00" in text:
            problems.append(f"{path}: contains a NUL byte")
            continue
        lines = trim_content(text)
        if not lines:
            problems.append(f"{path}: fragment is empty")
            continue
        # The tool adds the bullet marker and the continuation indent itself;
        # a fragment that already starts with one would render as "- - …".
        if lines[0].startswith(("- ", "* ", "+ ")) or lines[0][:1].isspace():
            problems.append(
                f"{path}: content must be the bullet's markdown without a leading "
                f"'- ' or indentation (the tool adds both)"
            )
            continue
        # `--pr-links` appends " (#N)" to the LAST line; glued onto a code-fence
        # closer that would break the markdown, so a fragment must end in prose.
        if lines[-1].lstrip().startswith(("```", "~~~")):
            problems.append(
                f"{path}: fragment must end with prose, not a code fence "
                f"(the PR link is appended to the last line)"
            )
            continue
        fragments.append(Fragment(path=path, id=frag_id, type=frag_type, lines=lines))
    if problems:
        raise FragmentError(problems)
    return fragments


def check_dirs(directories: Sequence[Path]) -> list[str]:
    """Validate every directory; return the combined list of problems."""
    problems: list[str] = []
    for directory in directories:
        try:
            load_dir(directory)
        except FragmentError as exc:
            problems.extend(exc.problems)
    return problems


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def render_bullet(fragment: Fragment, pr_links: bool = False) -> list[str]:
    """Render one fragment as a ``- `` bullet with two-space continuation indent."""
    lines = list(fragment.lines)
    if pr_links:
        pr = fragment.pr
        if pr is not None and not re.search(rf"#{pr}(?![0-9A-Za-z_])", "\n".join(lines)):
            lines[-1] = f"{lines[-1]} (#{pr})"
    out = [f"- {lines[0]}"]
    out.extend(f"  {line}" if line.strip() else "" for line in lines[1:])
    return out


def render_body(fragments: Sequence[Fragment], pr_links: bool = False) -> str:
    """Render the ``### …`` sections for ``fragments``; empty string when none."""
    by_type: dict[str, list[Fragment]] = {}
    for fragment in fragments:
        by_type.setdefault(fragment.type, []).append(fragment)
    sections: list[str] = []
    for frag_type, heading in TYPES:
        group = by_type.get(frag_type)
        if not group:
            continue
        # One blank line after the heading (Keep a Changelog style).
        lines = [f"### {heading}", ""]
        for fragment in sorted(group, key=lambda f: f.sort_key):
            lines.extend(render_bullet(fragment, pr_links=pr_links))
        sections.append("\n".join(lines))
    return "\n\n".join(sections) + ("\n" if sections else "")


# --------------------------------------------------------------------------- #
# Assembling
# --------------------------------------------------------------------------- #


def has_version(changelog: str, version: str) -> bool:
    return re.search(rf"^## \[{re.escape(version)}\]", changelog, re.MULTILINE) is not None


LINK_REF_RE = re.compile(r"^\[([^\]]+)\]:\s+\S+")


def previous_version(changelog: str) -> str | None:
    """The version of the first (newest) ``## [X.Y.Z]`` heading, if any."""
    match = re.search(r"^## \[([^\]]+)\]", changelog, re.MULTILINE)
    return match.group(1) if match else None


def add_link_reference(changelog: str, version: str, url: str) -> str:
    """Add ``[version]: url`` next to the existing reference-style link block.

    Keep a Changelog files define every version heading as a reference link at
    the bottom (``[0.3.2]: https://…/compare/…``). The new line goes directly
    above the first existing reference so newest stays first; with no block
    yet it is appended after one blank line.
    """
    lines = changelog.rstrip("\n").split("\n")
    idx = next((i for i, line in enumerate(lines) if LINK_REF_RE.match(line)), None)
    ref = f"[{version}]: {url}"
    if idx is None:
        return "\n".join(lines + ["", ref]) + "\n"
    return "\n".join(lines[:idx] + [ref] + lines[idx:]) + "\n"


def insert_release(changelog: str, version: str, date: str, body: str) -> str:
    """Insert ``## [version] — date`` + ``body`` before the first ``## [`` heading."""
    heading = f"## [{version}] — {date}"
    section = [heading, ""] + body.rstrip("\n").split("\n") + [""]
    lines = changelog.split("\n")
    idx = next((i for i, line in enumerate(lines) if HEADING_RE.match(line)), None)
    if idx is None:
        # No release heading yet: append at the end, separated by one blank line.
        while lines and lines[-1] == "":
            lines.pop()
        if lines:
            lines.append("")
        return "\n".join(lines + section[:-1]) + "\n"
    before = lines[:idx]
    if before and before[-1] != "":
        before.append("")
    return "\n".join(before + section + lines[idx:])


def assemble(
    directory: Path,
    changelog_path: Path,
    version: str,
    date: str,
    pr_links: bool,
    dry_run: bool,
    out=None,
    err=None,
    compare_url: str | None = None,
) -> int:
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    if not VERSION_RE.match(version):
        print(f"error: version {version!r} is not X.Y.Z[-pre]", file=err)
        return 1
    if compare_url is not None and "{version}" not in compare_url:
        print("error: --compare-url must contain {version} (and usually {prev})", file=err)
        return 1
    try:
        fragments = load_dir(directory)
    except FragmentError as exc:
        for problem in exc.problems:
            print(problem, file=err)
        return 1
    if not fragments:
        print(f"error: no changelog fragments in {directory}", file=err)
        return 1
    if not changelog_path.is_file():
        print(f"error: changelog {changelog_path} does not exist", file=err)
        return 1
    changelog = changelog_path.read_text(encoding="utf-8")
    if re.search(r"^## \[?Unreleased\]?\s*$", changelog, re.MULTILINE | re.IGNORECASE):
        print(
            f"error: {changelog_path} has an '## [Unreleased]' section — unreleased "
            f"changes are fragments in {directory}; move the bullets there first",
            file=err,
        )
        return 1
    if has_version(changelog, version):
        print(f"error: {changelog_path} already has a heading for {version}", file=err)
        return 1
    body = render_body(fragments, pr_links=pr_links)
    prev = previous_version(changelog)
    if compare_url is not None and "{prev}" in compare_url and prev is None:
        print(
            f"error: --compare-url uses {{prev}} but {changelog_path} has no earlier release",
            file=err,
        )
        return 1
    result = insert_release(changelog, version, date, body)
    if compare_url is not None:
        url = compare_url.replace("{version}", version).replace("{prev}", prev or "")
        result = add_link_reference(result, version, url)
    if dry_run:
        out.write(result)
        return 0
    # Write atomically (temp file + rename), then remove the fragments. A
    # failure in the unlink loop leaves the new section in place and says how
    # to recover; both files are tracked, so `git checkout` undoes either half.
    # The temp file is created with an unpredictable name and O_EXCL in the
    # changelog's own directory (same filesystem, so the rename is atomic), so a
    # pre-planted symlink at a guessable path can never be followed.
    fd, tmp_name = tempfile.mkstemp(prefix=f".{changelog_path.name}.", suffix=".tmp", dir=changelog_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(result)
        os.replace(tmp_name, changelog_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    removed = 0
    for fragment in fragments:
        try:
            fragment.path.unlink()
            removed += 1
        except OSError as exc:
            print(
                f"error: wrote {changelog_path} but could not remove {fragment.path}: "
                f"{exc.strerror}; remove the remaining fragments by hand (or "
                f"`git checkout -- {changelog_path}` to undo the release)",
                file=err,
            )
            return 1
    print(
        f"{changelog_path}: added [{version}] — {date} from {len(fragments)} "
        f"fragment(s); removed them from {directory}",
        file=err,
    )
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assemble.py",
        description="Validate, render, and fold per-PR changelog fragments.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="validate fragment names and content")
    p_check.add_argument(
        "--dir", action="append", required=True, type=Path, metavar="DIR",
        help="fragment directory (repeatable)",
    )

    p_render = sub.add_parser("render", help="print the assembled section body")
    p_render.add_argument("--dir", required=True, type=Path, metavar="DIR")
    p_render.add_argument(
        "--pr-links", action="store_true",
        help="append ' (#N)' to bullets whose id starts with PR number N",
    )

    p_asm = sub.add_parser("assemble", help="fold fragments into the changelog")
    p_asm.add_argument("--dir", required=True, type=Path, metavar="DIR")
    p_asm.add_argument("--changelog", required=True, type=Path, metavar="FILE")
    p_asm.add_argument("--version", required=True, metavar="X.Y.Z[-pre]")
    p_asm.add_argument(
        "--date", default=None, metavar="YYYY-MM-DD",
        help="release date (default: today, UTC)",
    )
    p_asm.add_argument("--pr-links", action="store_true")
    p_asm.add_argument(
        "--compare-url", default=None, metavar="TEMPLATE",
        help="also add a '[X.Y.Z]: URL' reference link; {version} and {prev} "
        "(the newest existing heading) are substituted, e.g. "
        "https://github.com/daytona/clients/compare/v{prev}...v{version}",
    )
    p_asm.add_argument(
        "--dry-run", action="store_true",
        help="print the resulting changelog to stdout; delete nothing",
    )
    return parser


def main(argv: Sequence[str] | None = None, out=None, err=None) -> int:
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    args = build_parser().parse_args(argv)

    if args.command == "check":
        problems = check_dirs(args.dir)
        for problem in problems:
            print(problem, file=err)
        if problems:
            print(f"{len(problems)} problem(s) found", file=err)
            return 1
        return 0

    if args.command == "render":
        try:
            fragments = load_dir(args.dir)
        except FragmentError as exc:
            for problem in exc.problems:
                print(problem, file=err)
            return 1
        out.write(render_body(fragments, pr_links=args.pr_links))
        return 0

    if args.command == "assemble":
        date = args.date or _today()
        try:
            _dt.date.fromisoformat(date)
        except ValueError:
            print(f"error: --date {date!r} is not YYYY-MM-DD", file=err)
            return 1
        return assemble(
            args.dir, args.changelog, args.version, date,
            pr_links=args.pr_links, dry_run=args.dry_run, out=out, err=err,
            compare_url=args.compare_url,
        )

    return 2  # unreachable: argparse enforces the subcommand


if __name__ == "__main__":
    sys.exit(main())
