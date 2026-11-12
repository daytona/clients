# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""BuildKit-compatible ignore filtering and ordered build-context traversal."""

from __future__ import annotations

import json
import os
import posixpath
import stat
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Final

from ._dockerignore_error import PatternError
from ._dockerignore_pattern import Pattern, clean, compile_pattern, validate_pattern
from ._image_source import utf8_path
from .errors import TransferCancelledError, create_error

_SPACE: Final = "\t\n\v\f\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"


def read_patterns(text: str) -> tuple[str, ...]:
    """Read ignorefile lines with Go scanner's 64-KiB token limit."""
    patterns: list[str] = []
    lines = text.split("\n")
    for index, line in enumerate(lines):
        # ScanLines needs space for the newline (or an EOF read).
        if len(line.encode("utf-8")) >= 65536:
            raise create_error("dockerignore line exceeds 64 KiB", 400, "BAD_REQUEST", "SDK")
        line = line.removesuffix("\r")
        if index == 0:
            line = line.removeprefix("\ufeff")
        if line.startswith("#"):
            continue
        value = line.strip(_SPACE)
        if not value:
            continue
        invert = value.startswith("!")
        if invert:
            value = value[1:].strip(_SPACE)
        if value:
            value = clean(value)
            if len(value) > 1 and value.startswith("/"):
                value = value[1:]
        patterns.append(("!" if invert else "") + value)
    return tuple(patterns)


def _without_trailing_glob(text: str) -> str:
    return text.removesuffix("/**").removesuffix("/*")


class Matcher:
    """Compile ignore patterns; carry each parent's per-pattern results down."""

    def __init__(self, patterns: Iterable[str]) -> None:
        compiled: list[Pattern] = []
        for raw in patterns:
            if not raw.strip(_SPACE):
                continue
            text = clean(raw.strip(_SPACE))
            exclusion = text.startswith("!")
            if exclusion:
                if len(text) == 1:
                    raise create_error(
                        f"invalid pattern {json.dumps(raw, ensure_ascii=False)}: illegal exclusion pattern",
                        400,
                        "BAD_REQUEST",
                        "SDK",
                    )
                text = text[1:]
            try:
                validate_pattern(text)
            except PatternError as error:
                raise create_error(
                    f"invalid pattern {json.dumps(raw, ensure_ascii=False)}: {error.reason}",
                    400,
                    "BAD_REQUEST",
                    "SDK",
                ) from error
            compiled.append(compile_pattern(text, raw, exclusion))
        self.patterns = tuple(compiled)
        self.exclusions = any(p.exclusion for p in compiled)
        self.wildcard_exceptions = any(
            p.exclusion and any(ch in _without_trailing_glob(p.text) for ch in "*[]?^\\") for p in compiled
        )

    def match(self, rel: str, parent: tuple[bool, ...] = ()) -> tuple[bool, tuple[bool, ...]]:
        """Return exclusion and the per-pattern state for this path's children."""
        rel = clean(rel)
        results = [False] * len(self.patterns)
        known = len(parent) == len(self.patterns) and bool(self.patterns)
        excluded = False
        for index, pattern in enumerate(self.patterns):
            matched = known and parent[index]
            if not matched:
                if pattern.exclusion != excluded:
                    continue
                if pattern.error is not None:
                    raise pattern.error
                matched = pattern.matches(rel)
                if not matched and not known:
                    directory = posixpath.dirname(rel)
                    if directory and directory != ".":
                        parts = directory.split("/")
                        matched = any(pattern.matches("/".join(parts[: i + 1])) for i in range(len(parts)))
            results[index] = matched
            if matched:
                excluded = not pattern.exclusion
        return excluded, tuple(results)

    def excluded(self, rel: str) -> bool:
        """Match a context path by visiting each parent first."""
        rel = clean(rel)
        if rel == ".":
            return False
        state: tuple[bool, ...] = ()
        excluded = False
        parts = rel.split("/")
        for index in range(len(parts)):
            excluded, state = self.match("/".join(parts[: index + 1]), state)
        return excluded

    def may_reinclude(self, directory: str) -> bool:
        """Whether a negation can reach below an excluded directory."""
        if not self.exclusions:
            return False
        if self.wildcard_exceptions:
            return True
        prefix = clean(directory) + "/"
        return any(
            p.exclusion and (_without_trailing_glob(p.text) + "/").startswith(prefix) for p in self.patterns
        )


class Filter:
    """Keep matcher-selected entries plus literal always-kept paths."""

    def __init__(self, matcher: Matcher, always: Iterable[str] = ()) -> None:
        self.matcher = matcher
        self.always = frozenset(clean(rel) for rel in always)
        directories: set[str] = set()
        for rel in self.always:
            directory = posixpath.dirname(rel)
            while directory and directory not in (".", "/"):
                directories.add(directory)
                directory = posixpath.dirname(directory)
        self.always_dirs = frozenset(directories)

    def keep(self, rel: str, parent: tuple[bool, ...] = ()) -> tuple[bool, bool, tuple[bool, ...]]:
        """Return keep, possible directory descent, and child matcher state."""
        excluded, state = self.matcher.match(rel, parent)
        keep = not excluded or rel in self.always
        descend = keep or rel in self.always_dirs or self.matcher.may_reinclude(rel)
        return keep, descend, state


@dataclass(frozen=True, slots=True)
class _Directory:
    path: Path
    state: tuple[bool, ...] = ()
    kept: bool = True
    pending: tuple[Path, ...] = ()


def walk_archive_entries(
    root: Path, filter: Filter, cancel: Event | None = None
) -> Iterator[tuple[str, Path]]:
    """Yield sorted archive entries (directory names end in '/'), never root.

    Excluded parents appear immediately before their first kept descendant.
    Symlinks are entries, never traversal roots. Pattern failures become SDK
    bad-request errors here, as do permission failures on kept directories.
    """
    if not stat.S_ISDIR(root.lstat().st_mode):
        return
    reported: set[Path] = set()

    def scan(directory: _Directory) -> Iterator[os.DirEntry[str]]:
        if cancel is not None and cancel.is_set():
            raise TransferCancelledError()
        try:
            with os.scandir(directory.path) as scanner:
                # UTF-8 byte order, as Go and TypeScript sort: surrogate-escaped
                # bytes would otherwise sort before U+E000-U+FFFF.
                return iter(sorted(scanner, key=lambda entry: os.fsencode(entry.name)))
        except PermissionError as error:
            if directory.kept:
                raise create_error(
                    f"build context directory is not readable: {json.dumps(str(directory.path), ensure_ascii=False)}",
                    400,
                    "BAD_REQUEST",
                    "SDK",
                ) from error
            return iter(())

    directory = _Directory(root)
    stack = [(directory, scan(directory))]
    try:
        while stack:
            directory, children = stack[-1]
            entry = next(children, None)
            if entry is None:
                stack.pop()
                continue
            if cancel is not None and cancel.is_set():
                raise TransferCancelledError()
            utf8_path(entry.path)
            path = Path(entry.path)
            rel = path.relative_to(root).as_posix()
            is_dir = entry.is_dir(follow_symlinks=False)
            keep, descend, state = filter.keep(rel, directory.state)
            if is_dir and not keep and not descend:
                continue
            if keep:
                for pending in directory.pending:
                    if pending not in reported:
                        yield pending.relative_to(root).as_posix() + "/", pending
                        reported.add(pending)
                yield rel + ("/" if is_dir else ""), path
            if is_dir:
                pending = directory.pending + ((path,) if not keep else ())
                child = _Directory(path, state, keep, pending)
                stack.append((child, scan(child)))
    except PatternError as error:
        raise create_error(str(error), 400, "BAD_REQUEST", "SDK") from error
