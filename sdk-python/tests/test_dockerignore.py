# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Run the CLI's unchanged BuildKit oracle through the Python archive walk."""

from __future__ import annotations

import contextlib
import json
import os
import re
import warnings
from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import pytest
from pydantic import TypeAdapter

from daytona.v2._dockerignore import Filter, Matcher, read_patterns, walk_archive_entries
from daytona.v2._dockerignore_class import translate_class
from daytona.v2._dockerignore_error import PatternError
from daytona.v2._dockerignore_pattern import _go_regexp
from daytona.v2.errors import BadRequestError


@dataclass(frozen=True, slots=True)
class _BuildKitCase:
    ignore: str
    files: tuple[str, ...]
    uploaded: tuple[str, ...]
    fails: bool = False


_CASES = TypeAdapter(tuple[_BuildKitCase, ...]).validate_json(
    (Path(__file__).parents[2] / "cli/internal/dockerignore/testdata/buildkit_cases.json").read_bytes()
)


@dataclass(frozen=True, slots=True)
class _MatcherCase:
    ignore: str
    paths: tuple[str, ...]
    excluded: tuple[bool, ...]


_MATCHER_CASES = TypeAdapter(tuple[_MatcherCase, ...]).validate_json(
    (Path(__file__).parent / "fixtures/dockerignore-edge-cases.json").read_bytes()
)


@pytest.mark.parametrize("case", _MATCHER_CASES, ids=lambda case: case.ignore)
def test_matcher_uses_go_semantics_without_warnings_when_edge_pattern(case: _MatcherCase) -> None:
    # Given valid Go patterns, including byte-sliced BOM suffixes and literal sets.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        # When compiling and evaluating every path. Building the matcher may
        # build a Unicode category table (a one-off cost of about a second,
        # longer on a loaded CI worker), so the time bound covers a second,
        # warm pass: it guards against backtracking, not table building.
        matcher = Matcher(read_patterns(case.ignore))
        actual = tuple(matcher.excluded(path) for path in case.paths)
        started = perf_counter()
        for path in case.paths:
            matcher.excluded(path)
        elapsed = perf_counter() - started
    # Then Go's membership semantics hold without Python extension warnings.
    assert actual == case.excluded
    assert elapsed < 1


def test_shared_buildkit_oracle_has_at_least_300_cases() -> None:
    # Given / When: load the same oracle as the Go suite.
    cases = _CASES
    # Then: a truncated corpus cannot silently pass.
    assert len(cases) >= 300


def test_long_literal_pattern_is_accepted_below_scanner_limit() -> None:
    # Given a literal longer than some engines can compile as a regex.
    pattern = "a" * 40000
    # When the matcher parses and evaluates the valid scanner-sized pattern.
    matcher = Matcher(read_patterns(pattern))
    # Then matching still follows the same exact-name rule in both SDKs.
    assert [matcher.excluded("different"), matcher.excluded(pattern)] == [False, True]


@pytest.mark.parametrize("case", _CASES, ids=[str(i) for i in range(len(_CASES))])
def test_walk_uploads_what_buildkit_uploads(tmp_path: Path, case: _BuildKitCase) -> None:
    # Given: exactly the tree in the shared oracle, including explicit empty dirs.
    for name in case.files:
        path = tmp_path / name
        if name.endswith("/"):
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")

    def upload() -> tuple[str, ...]:
        matcher = Matcher(read_patterns(case.ignore))
        return tuple(sorted(rel for rel, _ in walk_archive_entries(tmp_path, Filter(matcher))))

    # When / Then: fail on pattern errors only, or upload precisely BuildKit's set.
    if case.fails:
        with pytest.raises(BadRequestError, match="pattern") as raised:
            upload()
        assert (raised.value.status_code, raised.value.code, raised.value.source) == (
            400,
            "BAD_REQUEST",
            "SDK",
        )
    else:
        assert upload() == case.uploaded


def test_filter_always_keeps_literal_paths_with_excluded_parents(tmp_path: Path) -> None:
    # Given: forced paths contain glob characters and live under excluded dirs.
    (tmp_path / "d").mkdir()
    for name in ("d/Docker[1]file", "d/other", ".dockerignore"):
        (tmp_path / name).write_bytes(b"x")
    filter = Filter(Matcher(("d", ".dockerignore")), (".dockerignore", "d/Docker[1]file"))
    # When: visit entries in archive order.
    uploaded = [rel for rel, _ in walk_archive_entries(tmp_path, filter)]
    # Then: the excluded parent immediately precedes its first kept descendant.
    assert uploaded == [".dockerignore", "d/", "d/Docker[1]file"]


@pytest.mark.parametrize(
    "patterns", [("foo\\q",), ("x*/[",), ("[[::]",), ("[[:foo:]]",), ("ax[[::]",), ("[a*[]",)]
)
def test_pattern_compile_failure_is_delayed_until_evaluation(
    tmp_path: Path, patterns: tuple[str, ...]
) -> None:
    # Given: filepath.Match accepts these, but pattern compilation cannot.
    matcher = Matcher(patterns)
    (tmp_path / "x").write_bytes(b"x")
    # When: the walk evaluates the invalid pattern.
    with pytest.raises(BadRequestError) as raised:
        list(walk_archive_entries(tmp_path, Filter(matcher)))
    # Then: its internal PatternError is converted at the walk boundary.
    assert isinstance(raised.value.__cause__, PatternError)
    assert raised.value.__cause__.pattern == patterns[0]
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")


def test_skipped_invalid_exception_is_harmless(tmp_path: Path) -> None:
    # Given: no path is excluded when the invalid exception is reached.
    (tmp_path / "b").write_bytes(b"x")
    matcher = Matcher(("a", "!foo\\q"))
    # When: traverse the context.
    uploaded = [rel for rel, _ in walk_archive_entries(tmp_path, Filter(matcher))]
    # Then: an unevaluated pattern never fails the walk.
    assert uploaded == ["b"]


@pytest.mark.parametrize("pattern", ["!", "[a-", "["])
def test_initial_filepath_validation_refuses_bad_patterns(pattern: str) -> None:
    # Given: a pattern filepath.Match refuses before a filesystem walk.
    # When: compile it.
    with pytest.raises(BadRequestError, match="pattern") as raised:
        Matcher((pattern,))
    # Then: it is the SDK's configuration error, not a filesystem error.
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (400, "BAD_REQUEST", "SDK")


@pytest.mark.parametrize("ignore", ["data\n", "data\n!**/keep\n", "data\n!data/keep\n"])
def test_unreadable_excluded_directory_is_ignored(tmp_path: Path, ignore: str) -> None:
    # Given: a mode-000 excluded directory (root can still read it).
    if os.name == "nt" or os.geteuid() == 0:
        pytest.skip("requires Unix permissions without root privileges")
    (tmp_path / "Dockerfile").write_bytes(b"x")
    data = tmp_path / "data"
    data.mkdir()
    (data / "x").write_bytes(b"x")
    data.chmod(0)
    try:
        # When: traverse, including when an exception would normally descend.
        uploaded = [rel for rel, _ in walk_archive_entries(tmp_path, Filter(Matcher(read_patterns(ignore))))]
    finally:
        data.chmod(0o755)
    # Then: only the kept readable entry is uploaded.
    assert uploaded == ["Dockerfile"]


def test_pruned_directory_is_never_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a directory whose scan would fail, but which has no exceptions.
    data = tmp_path / "data"
    data.mkdir()
    (tmp_path / "kept").write_bytes(b"x")
    original = os.scandir

    def scan(path: Path) -> AbstractContextManager[Iterator[os.DirEntry[str]]]:
        if path == data:
            raise AssertionError("pruned directory was read")
        return original(path)

    monkeypatch.setattr(os, "scandir", scan)
    # When: traverse the filtered tree.
    uploaded = [rel for rel, _ in walk_archive_entries(tmp_path, Filter(Matcher(("data",))))]
    # Then: pruning happens before reading any child of data.
    assert uploaded == ["kept"]


def test_walk_archives_symlinks_without_traversing_them(tmp_path: Path) -> None:
    # Given: both a dangling link and a directory link (including a cycle).
    (tmp_path / "directory").mkdir()
    (tmp_path / "directory/file").write_bytes(b"x")
    (tmp_path / "link").symlink_to("directory", target_is_directory=True)
    (tmp_path / "directory/cycle").symlink_to("..", target_is_directory=True)
    (tmp_path / "dangling").symlink_to("absent")
    # When: traverse with no exclusions.
    uploaded = [rel for rel, _ in walk_archive_entries(tmp_path, Filter(Matcher(())))]
    # Then: symlinks are entries with no trailing slash and no traversal.
    assert uploaded == ["dangling", "directory/", "directory/cycle", "directory/file", "link"]


def test_walk_does_not_traverse_a_symlink_root(tmp_path: Path) -> None:
    # Given: the traversal root itself is a symlink, as opposed to a directory.
    directory = tmp_path / "directory"
    directory.mkdir()
    (directory / "file").write_bytes(b"x")
    root = tmp_path / "link"
    root.symlink_to(directory, target_is_directory=True)
    # When: walk the root, whose entry is always skipped.
    uploaded = list(walk_archive_entries(root, Filter(Matcher(()))))
    # Then: match Go WalkDir's refusal to traverse directory symlinks.
    assert uploaded == []


def test_unreadable_kept_directory_still_fails(tmp_path: Path) -> None:
    # Given: a kept mode-000 directory whose contents cannot be archived.
    if os.name == "nt" or os.geteuid() == 0:
        pytest.skip("requires Unix permissions without root privileges")
    data = tmp_path / 'data"é'
    data.mkdir()
    (data / "input").write_bytes(b"private")
    data.chmod(0)
    try:
        # When / Then: a kept directory reports a typed, JSON-quoted permission refusal.
        with pytest.raises(BadRequestError) as raised:
            list(walk_archive_entries(tmp_path, Filter(Matcher(("other",)))))
        assert (
            str(raised.value)
            == f"build context directory is not readable: {json.dumps(str(data), ensure_ascii=False)}"
        )
    finally:
        data.chmod(0o755)


def test_empty_context_never_evaluates_delayed_pattern_error(tmp_path: Path) -> None:
    # Given: a pattern that only fails when evaluated and no context entries.
    matcher = Matcher((r"foo\q",))
    # When: walk the empty directory.
    uploaded = list(walk_archive_entries(tmp_path, Filter(matcher)))
    # Then: neither the skipped root nor absent children evaluate any patterns.
    assert uploaded == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("\ufeff# comment\n\n  /secret.txt  \n! /keep/./me\n./a//b/\n", ("secret.txt", "!keep/me", "a/b")),
        (" #x\n#y\n", ("#x",)),
        ("\n\ufeff*.log\n", ("\ufeff*.log",)),
        ("a\rb\n", ("a\rb",)),
        ("\x1ca\x1c\n", ("\x1ca\x1c",)),
    ],
)
def test_read_patterns_follows_ignorefile_rules(text: str, expected: tuple[str, ...]) -> None:
    # Given: an ignore file exercising BOM, comment and path normalization rules.
    # When: read the ignore file.
    patterns = read_patterns(text)
    # Then: retain exactly the patterns Go's ignorefile parser would retain.
    assert patterns == expected


def test_oversized_line_is_refused_by_utf8_byte_length() -> None:
    # Given: fewer than 64 KiB of characters, but 64 KiB of UTF-8 bytes.
    text = "é" * 32768
    # When: read the ignore file.
    with pytest.raises(BadRequestError) as raised:
        read_patterns(text)
    # Then: apply the scanner's byte limit, not Python's character count.
    assert raised.value.code == "BAD_REQUEST"


@pytest.mark.parametrize(
    ("pattern", "path", "excluded"),
    [
        (r"\d", "7", True),
        (r"\d", "٧", False),
        (r"\w", "é", False),
        (r"\s", "\v", False),
        (r"\S", "\v", True),
        (r"\s", "\t", True),
        (r"\12", "\n", True),
        (r"\Qfoo\E", "foo", True),
        (r"foo\Q", "foo$tail", True),
        (r"\pL", "é", True),
        ("[[:alpha:]]", "a", True),
        ("[[:alpha:]]", "é", False),
        ("[[]", "[", True),
        ("[a[]", "[", True),
    ],
)
def test_pattern_regexp_uses_go_semantics(pattern: str, path: str, excluded: bool) -> None:
    # Given: escapes whose meaning differs between Go regexp and Python re.
    matcher = Matcher((pattern,))
    # When: evaluate the pattern against one path.
    actual = matcher.excluded(path)
    # Then: Go's classes, quoting and octal semantics decide the result.
    assert actual == excluded


@pytest.mark.parametrize("pattern", [r"\Z", r"\u0061", r"[\b]", r"\1"])
def test_python_only_regexp_syntax_fails_when_evaluated(tmp_path: Path, pattern: str) -> None:
    # Given: filepath.Match accepts a spelling that Go regexp refuses.
    matcher = Matcher((pattern,))
    (tmp_path / "a").write_bytes(b"x")
    # When: evaluate it while walking.
    with pytest.raises(BadRequestError) as raised:
        list(walk_archive_entries(tmp_path, Filter(matcher)))
    # Then: Python's additional regex syntax cannot silently change exclusion.
    assert isinstance(raised.value.__cause__, PatternError)


@pytest.mark.parametrize("escaped", ["€", "\u00a0"])
def test_non_ascii_escape_is_refused_when_compiling_go_regexp(escaped: str) -> None:
    # Given Go's parseEscape refuses even non-letter non-ASCII characters.
    expression = "a\\" + escaped
    # When compiling the regexp, then retain Go's precise internal reason.
    with pytest.raises(PatternError) as raised:
        _go_regexp(expression)
    assert raised.value.reason == "invalid escape sequence"


def test_go_regexp_preserves_range_when_set_difference_spelling() -> None:
    # Given a Go regexp class with a range ending in a literal hyphen.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        # When translated and compiled without Python set-operator ambiguity.
        pattern = re.compile(_go_regexp("^x[!--a]$"))
    # Then the range ! through - and the literal a retain Go's meaning.
    assert [pattern.fullmatch(path) is not None for path in ("x!", 'x"', "x-", "xa", "xb")] == [
        True,
        True,
        True,
        True,
        False,
    ]


def test_validating_set_syntax_emits_no_future_warning() -> None:
    # Go accepts these; Python's re warns about future set semantics, which
    # `-W error` would turn into an exception during a build.
    import warnings

    from daytona.v2._dockerignore import Matcher

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for pattern in ("a[[b]", "x[a&&b]", "x[a||b]", "x[a~~b]"):
            Matcher((pattern,)).excluded("abc")


@pytest.mark.parametrize("pattern", [r"\x4\101", r"a\x4", r"a\x{41}b"])
def test_a_hex_escape_without_two_digits_is_a_pattern_error(pattern: str) -> None:
    # Go refuses these (`{` reaches the regexp escaped); TypeScript too.
    with pytest.raises(PatternError):
        Matcher((pattern,)).excluded("a")


_REFUSED_CASES = json.loads((Path(__file__).parent / "fixtures/dockerignore-refused.json").read_text("utf-8"))


@pytest.mark.parametrize("case", _REFUSED_CASES, ids=lambda case: case["ignore"])
def test_matcher_refuses_what_go_refuses(case: dict[str, str]) -> None:
    # Given an ignore file Go's filepath.Match validation refuses (shared with
    # the Go and TypeScript suites), then building the matcher refuses it too.
    with pytest.raises(BadRequestError) as raised:
        Matcher(read_patterns(case["ignore"]))
    assert str(raised.value) == case["error"]


def test_a_long_run_of_posix_class_openers_is_scanned_once() -> None:
    # `[` followed by many `[:` with no `:]` used to search for `:]` once per
    # item (quadratic in the pattern). The class parser now searches once:
    # counted on a str subclass, since the cost of str.find is not visible to
    # a timer at a size CI can afford.
    class Counted(str):
        finds = 0

        def find(self, *args: object) -> int:  # type: ignore[override]  # counting wrapper
            Counted.finds += 1
            return super().find(*args)  # type: ignore[arg-type]

    source = Counted("[" + "[:x" * 3000 + "]")
    with contextlib.suppress(BadRequestError, PatternError):
        translate_class(source, 0)
    assert Counted.finds <= 2
