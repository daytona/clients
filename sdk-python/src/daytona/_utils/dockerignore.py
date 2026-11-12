# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Filtering of Docker build contexts with ``.dockerignore`` rules.

Matching follows Docker's own rules (moby/patternmatcher and the BuildKit context walker), not
``.gitignore`` rules: every pattern is anchored at the context root, ``*`` and ``?`` never cross a
``/``, ``**`` spans any number of directories, a later ``!`` rule re-includes what an earlier rule
excluded, and a rule that matches a directory excludes everything below it. A pattern Docker
rejects fails the upload instead of being skipped.

Docker turns a pattern into a Go regexp, and some regexp syntax reaches it unescaped: brackets,
escapes, ``|``, ``{n,m}`` and ``^``. That is translated to the Python equivalent. Unicode classes
(``\\p``) have no equivalent in Python's ``re`` module, so a pattern using one fails the upload.
"""

from __future__ import annotations

import os
import posixpath
import re
import tarfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import Enum

from ..common.errors import DaytonaValidationError

# Regexp metacharacters Docker escapes so that they match literally. Its escape table is meant to
# cover "{", "|" and "}" too but overflows for them, so those reach the regexp unescaped.
_LITERAL_REGEX_CHARS = frozenset(".+()$")
# A Go regexp repetition; any other "{" is a literal.
_REPEAT = re.compile(r"\{([0-9]+)(,([0-9]*))?\}")
# Go's limit on repetition counts.
_MAX_REPEAT = 1000
# Characters that make a pattern more than a plain path prefix.
_WILDCARD_CHARS = frozenset("*[]?^\\")
# Escapes that mean a character class in Go's regexp, and the same thing in Python's with re.ASCII.
_PERL_CLASS_ESCAPES = frozenset("dDsSwW")
# POSIX classes Go's regexp accepts in brackets, as Python class ranges.
_POSIX_CLASSES = {
    "alnum": "0-9A-Za-z",
    "alpha": "A-Za-z",
    "ascii": "\\x00-\\x7f",
    "blank": "\\t ",
    "cntrl": "\\x00-\\x1f\\x7f",
    "digit": "0-9",
    "graph": "!-~",
    "lower": "a-z",
    "print": " -~",
    "punct": "!-/:-@\\[-`{-~",
    "space": "\\t\\n\\v\\f\\r ",
    "upper": "A-Z",
    "word": "0-9A-Za-z_",
    "xdigit": "0-9A-Fa-f",
}


class _MatchType(Enum):
    EXACT = "exact"
    PREFIX = "prefix"
    SUFFIX = "suffix"
    REGEX = "regex"


@dataclass(frozen=True)
class _Pattern:
    cleaned: str
    exclusion: bool
    match_type: _MatchType
    regex: re.Pattern[str] | None

    def match(self, path: str) -> bool:
        if self.match_type is _MatchType.EXACT:
            return path == self.cleaned
        if self.match_type is _MatchType.PREFIX:
            return path.startswith(self.cleaned[:-2])
        if self.match_type is _MatchType.SUFFIX:
            suffix = self.cleaned[2:]
            return path.endswith(suffix) or (suffix.startswith("/") and path == suffix[1:])
        if self.regex is None:
            return False
        # Like Go's MatchString, the regexp may match anywhere; its own anchors decide.
        return self.regex.search(path) is not None

    def without_trailing_glob(self) -> str:
        return self.cleaned.removesuffix("/**").removesuffix("/*")


def _clean(pattern: str) -> str:
    """Lexically clean a pattern the way Go's ``filepath.Clean`` does."""
    cleaned = posixpath.normpath(pattern)
    # normpath keeps exactly two leading slashes, Clean folds them into one.
    return "/" + cleaned.lstrip("/") if cleaned.startswith("//") else cleaned


def _glob_chunks(pattern: str) -> Iterator[str]:
    """Split a pattern at unbracketed stars, as Go's ``filepath.Match`` scans it."""
    while pattern:
        pattern = pattern.lstrip("*")
        in_range = False
        index = 0
        while index < len(pattern):
            char = pattern[index]
            if char == "\\":
                if index + 1 < len(pattern):
                    index += 1
            elif char == "[":
                in_range = True
            elif char == "]":
                in_range = False
            elif char == "*" and not in_range:
                break
            index += 1
        yield pattern[:index]
        pattern = pattern[index:]


def _skip_glob_class_char(chunk: str, index: int) -> int:
    """Step over one bracket expression character the way Go's ``filepath.Match`` reads it."""
    if index >= len(chunk) or chunk[index] in "-]":
        raise ValueError("syntax error in pattern")
    if chunk[index] == "\\":
        index += 1
        if index >= len(chunk):
            raise ValueError("syntax error in pattern")
    index += 1
    if index >= len(chunk):
        raise ValueError("syntax error in pattern")
    return index


def _check_glob_syntax(pattern: str) -> None:
    """Reject a pattern that Go's ``filepath.Match`` reports as malformed, as Docker does."""
    for chunk in _glob_chunks(pattern):
        index = 0
        while index < len(chunk):
            char = chunk[index]
            index += 1
            if char == "[":
                if index < len(chunk) and chunk[index] == "^":
                    index += 1
                ranges = 0
                while not (index < len(chunk) and chunk[index] == "]" and ranges > 0):
                    index = _skip_glob_class_char(chunk, index)
                    if chunk[index] == "-":
                        index = _skip_glob_class_char(chunk, index + 1)
                    ranges += 1
                index += 1
            elif char == "\\":
                if index >= len(chunk):
                    raise ValueError("syntax error in pattern")
                index += 1


_CONTROL_ESCAPES = {"a": "\a", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
# Go's assertions, as Python spells them. Go's \z is Python's \Z.
_ASSERTION_ESCAPES = {"A": "\\A", "b": "\\b", "B": "\\B", "z": "\\Z"}
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_OCTAL_DIGITS = frozenset("01234567")


def _escaped_char(regex: str, index: int) -> tuple[str, int] | None:
    """Read the character a Go regexp escape at ``regex[index]`` stands for.

    Returns ``None`` for an escape that is not a single character, such as a class or assertion.
    """
    if index + 1 >= len(regex):
        raise ValueError("trailing backslash")
    char = regex[index + 1]
    if char in _CONTROL_ESCAPES:
        return _CONTROL_ESCAPES[char], index + 2
    if char == "x":
        if regex[index + 2 : index + 3] == "{":
            end = regex.find("}", index + 3)
            digits = regex[index + 3 : end] if end != -1 else ""
            if not digits or not _HEX_DIGITS.issuperset(digits) or int(digits, 16) > 0x10FFFF:
                raise ValueError("invalid escape \\x{...}")
            return chr(int(digits, 16)), end + 1
        digits = regex[index + 2 : index + 4]
        if len(digits) != 2 or not _HEX_DIGITS.issuperset(digits):
            raise ValueError("invalid escape \\x")
        return chr(int(digits, 16)), index + 4
    if char in _OCTAL_DIGITS:
        # A single non-zero digit would be a backreference, which Go does not support.
        end = index + 2
        while end < index + 4 and regex[end : end + 1] in _OCTAL_DIGITS and regex[end : end + 1]:
            end += 1
        if char != "0" and end == index + 2:
            raise ValueError(f"invalid escape \\{char}")
        return chr(int(regex[index + 1 : end], 8)), end
    if char in _PERL_CLASS_ESCAPES or (char.isascii() and char.isalnum()):
        return None
    if not char.isascii():
        raise ValueError(f"invalid escape \\{char}")
    return char, index + 2


def _regex_class_char(regex: str, index: int) -> tuple[str, str, int]:
    """Read one literal character of a Go regexp class as a Python class fragment."""
    if regex[index] != "\\":
        return re.escape(regex[index]), regex[index], index + 1
    escaped = _escaped_char(regex, index)
    if escaped is None:
        raise ValueError(f"unsupported escape \\{regex[index + 1]} in a character class")
    char, index = escaped
    return re.escape(char), char, index


def _translate_regex_class(regex: str, index: int) -> tuple[str, int]:
    """Translate a Go regexp bracket expression starting after its ``[``."""
    translated = "["
    if index < len(regex) and regex[index] == "^":
        translated += "^"
        index += 1
    first = True
    while True:
        if index >= len(regex):
            raise ValueError("missing closing ]")
        if regex[index] == "]" and not first:
            return translated + "]", index + 1
        first = False
        if regex.startswith("[:", index) and (end := regex.find(":]", index + 2)) != -1:
            name = regex[index + 2 : end]
            if name not in _POSIX_CLASSES:
                raise ValueError(f"unsupported character class [:{name}:]")
            translated += _POSIX_CLASSES[name]
            index = end + 2
            continue
        if regex[index] == "\\" and regex[index + 1 : index + 2] in _PERL_CLASS_ESCAPES:
            translated += regex[index : index + 2]
            index += 2
            continue
        low_fragment, low, index = _regex_class_char(regex, index)
        if index + 1 < len(regex) and regex[index] == "-" and regex[index + 1] != "]":
            high_fragment, high, index = _regex_class_char(regex, index + 1)
            if high < low:
                raise ValueError("invalid character class range")
            translated += f"{low_fragment}-{high_fragment}"
        else:
            translated += low_fragment


def _translate_escape(regex: str, index: int) -> tuple[str, int]:
    """Translate a Go regexp escape outside a character class."""
    escaped = _escaped_char(regex, index)
    if escaped is not None:
        char, index = escaped
        return re.escape(char), index
    char = regex[index + 1]
    if char in _PERL_CLASS_ESCAPES:
        return regex[index : index + 2], index + 2
    if char in _ASSERTION_ESCAPES:
        return _ASSERTION_ESCAPES[char], index + 2
    if char == "Q":
        # Everything up to \E, or the end, is literal text.
        end = regex.find("\\E", index + 2)
        literal = regex[index + 2 :] if end == -1 else regex[index + 2 : end]
        return re.escape(literal), len(regex) if end == -1 else end + 2
    # Unicode classes (\p, \P) have no equivalent in Python's re module.
    raise ValueError(f"unsupported escape \\{char}")


def _translate_regex(regex: str) -> str:
    """Translate the Go regexp Docker builds for a pattern into the Python equivalent."""
    translated = ""
    index = 0
    while index < len(regex):
        char = regex[index]
        if char == "[":
            fragment, index = _translate_regex_class(regex, index + 1)
            translated += fragment
        elif char == "\\":
            fragment, index = _translate_escape(regex, index)
            translated += fragment
        elif char == "{":
            repeat = _REPEAT.match(regex, index)
            if repeat is None:
                translated += "\\{"
                index += 1
                continue
            low = int(repeat.group(1))
            high = None if not repeat.group(3) else int(repeat.group(3))
            if low > _MAX_REPEAT or (high is not None and (high > _MAX_REPEAT or high < low)):
                raise ValueError(f"invalid repeat count {repeat.group(0)}")
            translated += repeat.group(0)
            index = repeat.end()
        elif char == "$":
            # Go's "$" only matches at the very end, Python's also before a final newline.
            translated += "\\Z"
            index += 1
        else:
            translated += char
            index += 1
    return translated


def _compile(cleaned: str, exclusion: bool) -> _Pattern:
    """Translate one pattern into a matcher, raising ``ValueError`` if Docker would reject it."""
    _check_glob_syntax(cleaned)
    regex = "^"
    match_type = _MatchType.EXACT
    position = 0
    index = 0
    while position < len(cleaned):
        char = cleaned[position]
        position += 1
        if char == "*":
            if position < len(cleaned) and cleaned[position] == "*":
                position += 1
                # "**/" behaves like "**".
                if position < len(cleaned) and cleaned[position] == "/":
                    position += 1
                if position == len(cleaned):
                    if match_type is _MatchType.EXACT:
                        match_type = _MatchType.PREFIX
                    else:
                        regex += ".*"
                        match_type = _MatchType.REGEX
                else:
                    regex += "(.*/)?"
                    match_type = _MatchType.REGEX
                if index == 0:
                    match_type = _MatchType.SUFFIX
            else:
                regex += "[^/]*"
                match_type = _MatchType.REGEX
        elif char == "?":
            regex += "[^/]"
            match_type = _MatchType.REGEX
        elif char in _LITERAL_REGEX_CHARS:
            regex += "\\" + char
        elif char == "\\":
            if position < len(cleaned):
                regex += "\\" + cleaned[position]
                position += 1
                match_type = _MatchType.REGEX
            else:
                regex += "\\"
        elif char in "[]":
            regex += char
            match_type = _MatchType.REGEX
        else:
            regex += char
        index += 1

    if match_type is not _MatchType.REGEX:
        return _Pattern(cleaned, exclusion, match_type, None)
    # Docker's regexp is a Go one. Translate it so that it matches the same paths in Python.
    try:
        compiled = re.compile(_translate_regex(regex + "$"), re.ASCII)
    except re.error as error:
        raise ValueError(str(error)) from error
    return _Pattern(cleaned, exclusion, match_type, compiled)


def _parse(content: str, source: str) -> list[_Pattern]:
    """Parse ``.dockerignore`` content into patterns, in file order.

    Raises:
        DaytonaValidationError: If a line holds a pattern Docker rejects.
    """
    patterns: list[_Pattern] = []
    for line_number, raw_line in enumerate(content.split("\n")):
        line = raw_line.removesuffix("\r")
        if line_number == 0:
            line = line.removeprefix("\ufeff")
        if line.startswith("#"):
            continue
        line = line.strip()
        if not line:
            continue
        if os.sep == "\\":
            # On Windows a backslash is a path separator, never an escape.
            line = line.replace("\\", "/")
        exclusion = line.startswith("!")
        if exclusion:
            line = line[1:].strip()
        if line:
            line = _clean(line)
            # A leading slash is redundant: every pattern is relative to the context root.
            if len(line) > 1 and line.startswith("/"):
                line = line[1:]
        try:
            if exclusion and not line:
                raise ValueError('illegal exclusion pattern: "!"')
            patterns.append(_compile(line, exclusion))
        except ValueError as error:
            raise DaytonaValidationError(
                f"{source}:{line_number + 1}: invalid pattern {raw_line.strip()!r}: {error}"
            ) from error
    return patterns


@dataclass(frozen=True)
class DockerIgnore:
    """The ``.dockerignore`` rules of one build context."""

    context_root: str
    patterns: tuple[_Pattern, ...]
    _has_exclusions: bool = field(init=False)
    _only_prefix_exclusions: bool = field(init=False)

    def __post_init__(self) -> None:
        exclusions = [pattern for pattern in self.patterns if pattern.exclusion]
        object.__setattr__(self, "_has_exclusions", bool(exclusions))
        object.__setattr__(
            self,
            "_only_prefix_exclusions",
            not any(_WILDCARD_CHARS.intersection(pattern.without_trailing_glob()) for pattern in exclusions),
        )

    @classmethod
    def load(cls, dockerignore_path: str | None) -> DockerIgnore | None:
        """Read the rules at ``dockerignore_path``.

        Returns ``None`` when there is no such file or it has no rules, so that callers can keep
        the unfiltered behaviour for contexts without a ``.dockerignore``.

        Raises:
            DaytonaValidationError: If the file holds a pattern Docker rejects.
        """
        if dockerignore_path is None or not os.path.isfile(dockerignore_path):
            return None
        with open(dockerignore_path, encoding="utf-8", errors="surrogateescape") as dockerignore_file:
            patterns = _parse(dockerignore_file.read(), dockerignore_path)
        if not patterns:
            return None
        return cls(os.path.dirname(os.path.abspath(dockerignore_path)), tuple(patterns))

    def relative(self, path: str) -> str | None:
        """Return ``path`` relative to the context root, or ``None`` if it lies outside it."""
        try:
            relative = os.path.relpath(os.path.abspath(path), self.context_root)
        except ValueError:  # a different drive on Windows
            return None
        relative = relative.replace(os.sep, "/")
        if relative == ".." or relative.startswith("../"):
            return None
        return relative

    def match(self, relative: str, parent: tuple[bool, ...] | None) -> tuple[bool, tuple[bool, ...]]:
        """Match a context-relative path the way BuildKit does while it walks the context.

        ``parent`` holds, per pattern, whether it matched the parent directory (``None`` for an
        entry at the context root). Returns whether the path is excluded and the per-pattern results
        to pass on to its children. A pattern that matched a parent applies to every entry below
        it, and one skipped at the parent is checked against the entry itself only.
        """
        excluded = False
        results: list[bool] = []
        for index, pattern in enumerate(self.patterns):
            matched = parent is not None and parent[index]
            if not matched:
                # An exclusion can only change an excluded path, an inclusion an included one.
                if pattern.exclusion != excluded:
                    results.append(False)
                    continue
                matched = pattern.match(relative)
            results.append(matched)
            if matched:
                excluded = not pattern.exclusion
        return excluded, tuple(results)

    def excludes(self, relative: str) -> bool:
        """Whether a context-relative path is excluded, given the directories above it."""
        if relative == ".":
            # Docker never excludes the context root itself.
            return False
        parts = relative.split("/")
        parent = None
        for depth in range(1, len(parts)):
            _, parent = self.match("/".join(parts[:depth]), parent)
        return self.match(relative, parent)[0]

    def may_include_below(self, relative_dir: str) -> bool:
        """Whether a later ``!`` rule could re-include something inside an excluded directory."""
        if not self._has_exclusions:
            return False
        if not self._only_prefix_exclusions:
            return True
        dir_slash = relative_dir + "/"
        return any(
            (pattern.without_trailing_glob() + "/").startswith(dir_slash)
            for pattern in self.patterns
            if pattern.exclusion
        )


@dataclass
class _PendingDir:
    """An excluded directory that is archived only if something below it is re-included."""

    local_path: str
    archive_path: str
    emitted: bool = False


def _is_real_dir(path: str) -> bool:
    return os.path.isdir(path) and not os.path.islink(path)


def _walk(
    local_path: str,
    archive_path: str,
    relative: str | None,
    parent: tuple[bool, ...] | None,
    dockerignore: DockerIgnore,
    pending: list[_PendingDir],
) -> Iterator[tuple[str, str]]:
    is_dir = _is_real_dir(local_path)
    excluded = False
    results = None
    if relative is not None and relative != ".":
        excluded, results = dockerignore.match(relative, parent)
    if excluded:
        if not is_dir or not dockerignore.may_include_below(relative or ""):
            return
        pending = [*pending, _PendingDir(local_path, archive_path)]
    else:
        for directory in pending:
            if not directory.emitted:
                directory.emitted = True
                yield directory.local_path, directory.archive_path
        pending = []
        yield local_path, archive_path

    if not is_dir:
        return
    # Same order as tarfile's own recursive add, so an archive that excludes nothing is unchanged.
    for name in sorted(os.listdir(local_path)):
        child_relative = None if relative is None else (name if relative == "." else f"{relative}/{name}")
        yield from _walk(
            os.path.join(local_path, name),
            posixpath.join(archive_path, name),
            child_relative,
            results,
            dockerignore,
            pending,
        )


def iter_context_entries(
    source_path: str, archive_base_path: str, dockerignore: DockerIgnore
) -> Iterator[tuple[str, str]]:
    """Yield ``(local path, archive path)`` for every entry of a source that the rules keep.

    Entries come in the order ``tarfile`` adds them. Symlinks are kept as links and never
    followed, and a source outside the context root is not filtered at all.
    """
    relative = dockerignore.relative(source_path)
    parent = None
    if relative is not None and relative != ".":
        # Match the directories above the source as BuildKit's walk from the context root would.
        parts = relative.split("/")
        for depth in range(1, len(parts)):
            ancestor = "/".join(parts[:depth])
            excluded, parent = dockerignore.match(ancestor, parent)
            if excluded and not dockerignore.may_include_below(ancestor):
                return
    yield from _walk(source_path, archive_base_path, relative, parent, dockerignore, [])


def add_context_to_tar(
    tar: tarfile.TarFile, source_path: str, archive_base_path: str, dockerignore: DockerIgnore
) -> None:
    """Add the entries of a source that the rules keep to ``tar``."""
    for local_path, archive_path in iter_context_entries(source_path, archive_base_path, dockerignore):
        tar.add(local_path, arcname=archive_path, recursive=False)


def hash_context_entries(
    update: Callable[[bytes], None], source_path: str, archive_base_path: str, dockerignore: DockerIgnore
) -> None:
    """Feed exactly the entries `add_context_to_tar` archives into a hash's ``update``."""
    for local_path, archive_path in iter_context_entries(source_path, archive_base_path, dockerignore):
        name = os.fsencode(archive_path)
        if os.path.islink(local_path):
            update(b"l\0" + name + b"\0" + os.fsencode(os.readlink(local_path)) + b"\0")
        elif os.path.isdir(local_path):
            update(b"d\0" + name + b"\0")
        else:
            update(b"f\0" + name + b"\0" + str(os.path.getsize(local_path)).encode() + b"\0")
            with open(local_path, "rb") as file:
                while chunk := file.read(8192):
                    update(chunk)
