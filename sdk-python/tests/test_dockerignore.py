# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from pathlib import Path

import pytest

from daytona._utils.dockerignore import DockerIgnore, iter_context_entries
from daytona.common.errors import DaytonaValidationError


def _load(tmp_path: Path, rules: str) -> DockerIgnore:
    dockerignore_path = tmp_path / ".dockerignore"
    dockerignore_path.write_text(rules, encoding="utf-8")
    dockerignore = DockerIgnore.load(str(dockerignore_path))
    assert dockerignore is not None
    return dockerignore


def _archive_paths(source: Path, dockerignore: DockerIgnore) -> list[str]:
    return [archive_path for _local_path, archive_path in iter_context_entries(str(source), "ctx", dockerignore)]


class TestMatching:
    # Expectations follow what `docker build` sends for the same rules.
    @pytest.mark.parametrize(
        ("rules", "path", "excluded"),
        [
            # Patterns are anchored at the context root, unlike .gitignore.
            ("*.pyc", "a.pyc", True),
            ("*.pyc", "src/a.pyc", False),
            ("node_modules", "node_modules/m/index.js", True),
            ("node_modules", "src/node_modules/index.js", False),
            ("README.md", "docs/README.md", False),
            # "**" spans directories, including none.
            ("**/*.pyc", "a.pyc", True),
            ("**/*.pyc", "src/deep/a.pyc", True),
            ("**/node_modules", "src/node_modules/index.js", True),
            ("data/**", "data/sub/3.csv", True),
            ("x/**/z.txt", "x/z.txt", True),
            ("x/**/z.txt", "x/y/w/z.txt", True),
            # "*" and "?" stay within one path segment.
            ("*", "dir/file", True),
            ("dir/*", "dir/sub/file", True),
            ("foo?bar", "fooXbar", True),
            ("foo?bar", "foo/bar", False),
            ("[ab].py", "a.py", True),
            ("[ab].py", "c.py", False),
            ("weird\\[1\\].txt", "weird[1].txt", True),
            # Leading and trailing slashes and dot segments are cleaned away.
            ("/README.md", "README.md", True),
            (".venv/", ".venv/bin/python", True),
            ("x/../logs", "logs/a.log", True),
            # Later rules override earlier ones.
            (".venv\n!.venv/keep.txt", ".venv/keep.txt", False),
            (".venv\n!.venv/keep.txt", ".venv/bin/python", True),
            ("*.py\n!*.py", "a.py", False),
            ("*.py\n!*.py\n*.py", "a.py", True),
            # A rule skipped for a parent directory is not checked against that parent again, so
            # repeating an exclusion after an exception does not re-exclude what is inside it.
            ("d\n!d/keep\nd", "d/keep", False),
            ("d\n!d/keep\nd", "d/other", True),
            ("d\n!d/keep\n**/d", "d/keep", False),
            ("node_modules\n!node_modules/local\n**/node_modules", "node_modules/local/index.js", False),
            ("node_modules\n!node_modules/local\n**/node_modules", "src/node_modules/index.js", True),
            ("d\n!d/sub\nd/sub/keep", "d/sub/keep", True),
            ("d\n!d/sub\nd/sub/keep", "d/sub/other", False),
            # Bracket expressions follow Go's regexp, POSIX classes included.
            ("[[:alpha:]]*", "app.py", True),
            ("[[:alpha:]]*", "9.txt", False),
            ("[[:digit:][:upper:]]*", "Upper.txt", True),
            ("[^a-m]*", "zeta", True),
            ("[^a-m]*", "alpha", False),
            ("[!a]*", "!x", True),
            ("\\d*", "9.txt", True),
            # Go regexp escapes, alternation and repetition reach Docker's regexp as they are.
            ("\\x41*", "A1", True),
            ("\\x{55}*", "Upper.txt", True),
            ("\\101*", "A1", True),
            ("tab\\tx", "tab\tx", True),
            ("[\\x41-\\x5a]*", "Upper.txt", True),
            ("\\Qa\\E.py", "a.py", True),
            ("a|U*", "app.py", True),
            ("a|U*", "Upper.txt", True),
            ("a|U*", "b.py", False),
            ("*.p{1}y", "a.py", True),
            ("x{,2}*", "x{,2}z", True),
            ("*.{py,txt}", "a.{py,txt}", True),
            ("*.{py,txt}", "a.py", False),
            ("Up$*", "Up$1", True),
            ("Up$*", "Up", False),
            # Comments, blank lines and "." match nothing.
            ("# a.py\n\n.", "a.py", False),
            ("\ufeffa.py\r\n", "a.py", True),
        ],
    )
    def test_matches_like_docker(self, tmp_path: Path, rules: str, path: str, excluded: bool):
        assert _load(tmp_path, rules).excludes(path) is excluded

    # Docker fails the build on each of these, so uploading the rest would fail open.
    @pytest.mark.parametrize(
        "rule",
        [
            "[",
            "!",
            "abc\\",
            "[]a]",
            "[-a]",
            "[a-]",
            "[z-a]",
            "[[:foo:]]",
            "[[:^alpha:]]",
            "a{1001}*",
            "b{2,1}*",
            "*{2}",
            "\\1a",
        ],
    )
    def test_invalid_pattern_raises(self, tmp_path: Path, rule: str):
        dockerignore_path = tmp_path / ".dockerignore"
        dockerignore_path.write_text(f"*.log\n{rule}\n", encoding="utf-8")

        with pytest.raises(DaytonaValidationError, match=rf"\.dockerignore:2: invalid pattern"):
            _ = DockerIgnore.load(str(dockerignore_path))

    def test_unicode_class_is_unsupported(self, tmp_path: Path):
        # Docker accepts \p{Lu}, but Python's re module has no Unicode classes.
        dockerignore_path = tmp_path / ".dockerignore"
        dockerignore_path.write_text("\\p{Lu}*\n", encoding="utf-8")

        with pytest.raises(DaytonaValidationError, match=r"unsupported escape \\p"):
            _ = DockerIgnore.load(str(dockerignore_path))

    def test_backslash_is_a_separator_on_windows(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(os, "sep", "\\")

        dockerignore = _load(tmp_path, "build\\out\n")

        assert dockerignore.excludes("build/out/a.o") is True
        assert dockerignore.excludes("build/keep.txt") is False

    def test_context_root_is_never_excluded(self, tmp_path: Path):
        assert _load(tmp_path, "**").excludes(".") is False

    def test_missing_or_empty_dockerignore_loads_as_none(self, tmp_path: Path):
        assert DockerIgnore.load(None) is None
        assert DockerIgnore.load(str(tmp_path / ".dockerignore")) is None
        (tmp_path / ".dockerignore").write_text("# nothing\n\n", encoding="utf-8")
        assert DockerIgnore.load(str(tmp_path / ".dockerignore")) is None

    def test_paths_outside_the_context_are_not_filtered(self, tmp_path: Path):
        context = tmp_path / "ctx"
        context.mkdir()
        outside = tmp_path / "elsewhere.txt"
        outside.write_text("outside", encoding="utf-8")
        dockerignore = _load(context, "*\n")

        assert dockerignore.relative(str(outside)) is None
        assert _archive_paths(outside, dockerignore) == ["ctx"]


class TestContextEntries:
    def test_keeps_tarfile_order_and_skips_excluded_entries(self, tmp_path: Path):
        (tmp_path / "src/deep").mkdir(parents=True)
        (tmp_path / "src/deep/b.py").write_text("b", encoding="utf-8")
        (tmp_path / "src/a.pyc").write_text("a", encoding="utf-8")
        (tmp_path / "a.pyc").write_text("a", encoding="utf-8")
        (tmp_path / "app.py").write_text("app", encoding="utf-8")
        dockerignore = _load(tmp_path, "*.pyc\n")

        assert _archive_paths(tmp_path, dockerignore) == [
            "ctx",
            "ctx/.dockerignore",
            "ctx/app.py",
            "ctx/src",
            "ctx/src/a.pyc",
            "ctx/src/deep",
            "ctx/src/deep/b.py",
        ]

    def test_reincluded_file_brings_its_excluded_parents(self, tmp_path: Path):
        (tmp_path / ".venv/lib").mkdir(parents=True)
        (tmp_path / ".venv/lib/keep.txt").write_text("keep", encoding="utf-8")
        (tmp_path / ".venv/python3").write_text("host interpreter", encoding="utf-8")
        dockerignore = _load(tmp_path, ".venv\n!.venv/lib/keep.txt\n")

        assert _archive_paths(tmp_path, dockerignore) == [
            "ctx",
            "ctx/.dockerignore",
            "ctx/.venv",
            "ctx/.venv/lib",
            "ctx/.venv/lib/keep.txt",
        ]

    def test_excluded_source_directory_is_left_out_entirely(self, tmp_path: Path):
        (tmp_path / ".venv/bin").mkdir(parents=True)
        (tmp_path / ".venv/bin/python3").write_text("host interpreter", encoding="utf-8")
        dockerignore = _load(tmp_path, ".venv\n")

        assert _archive_paths(tmp_path / ".venv", dockerignore) == []
        assert _archive_paths(tmp_path / ".venv/bin/python3", dockerignore) == []

    def test_nested_source_uses_the_rules_of_its_parent_directories(self, tmp_path: Path):
        (tmp_path / "d/sub").mkdir(parents=True)
        (tmp_path / "d/keep").write_text("keep", encoding="utf-8")
        (tmp_path / "d/other").write_text("other", encoding="utf-8")
        (tmp_path / "d/sub/keep").write_text("keep", encoding="utf-8")
        reapplied = _load(tmp_path, "d\n!d/keep\nd\n")

        assert _archive_paths(tmp_path / "d", reapplied) == ["ctx", "ctx/keep"]
        assert _archive_paths(tmp_path / "d/keep", reapplied) == ["ctx"]
        assert _archive_paths(tmp_path / "d/sub/keep", _load(tmp_path, "d\n")) == []

    def test_excluded_directory_is_not_walked_without_exceptions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / "node_modules/pkg").mkdir(parents=True)
        (tmp_path / "node_modules/pkg/index.js").write_text("js", encoding="utf-8")
        dockerignore = _load(tmp_path, "node_modules\n!keep.txt\n")
        listed: list[str] = []
        real_listdir = os.listdir

        def recording_listdir(path: str) -> list[str]:
            listed.append(os.path.relpath(path, tmp_path))
            return real_listdir(path)

        monkeypatch.setattr(os, "listdir", recording_listdir)

        assert _archive_paths(tmp_path, dockerignore) == ["ctx", "ctx/.dockerignore"]
        assert listed == ["."]

    def test_symlinks_are_kept_as_links_and_not_followed(self, tmp_path: Path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        context = tmp_path / "ctx"
        context.mkdir()
        (context / "link").symlink_to(outside, target_is_directory=True)
        dockerignore = _load(context, "*.log\n")

        assert _archive_paths(context, dockerignore) == ["ctx", "ctx/.dockerignore", "ctx/link"]
        assert _archive_paths(context / "link", dockerignore) == ["ctx"]
