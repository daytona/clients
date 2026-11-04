# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for scripts/changelog/assemble.py (stdlib unittest, tempfile only).

Run: python3 -m unittest discover -s scripts/changelog -v
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import assemble  # noqa: E402


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = assemble.main(list(argv), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.frag_dir = self.root / "changelog.d"
        self.frag_dir.mkdir()
        (self.frag_dir / "README.md").write_text("ignored by the tool\n")

    def frag(self, name: str, text: str) -> Path:
        path = self.frag_dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def changelog(self, text: str) -> Path:
        path = self.root / "CHANGELOG.md"
        path.write_text(text, encoding="utf-8")
        return path


class NameValidation(TempDirCase):
    def test_good_ids_and_types(self) -> None:
        for name in (
            "1234.added.md",
            "1234-watch-exit.added.md",
            "signed-preview-links.fixed.md",
            "1.breaking.md",
            "0-a.security.md",
            "a1-b2.deprecated.md",
            "x.removed.md",
            "7.changed.md",
        ):
            self.assertIsInstance(assemble.parse_name(name), tuple, name)

    def test_bad_ids(self) -> None:
        for name in (
            "Fix.added.md",  # uppercase
            "-slug.added.md",  # leading hyphen
            "12_34.added.md",  # underscore
            "with space.added.md",
            "1234-Watch.added.md",
        ):
            self.assertIsInstance(assemble.parse_name(name), str, name)

    def test_digit_led_slugs_are_accepted_by_the_plain_slug_rule(self) -> None:
        # The contract's second alternative (^[a-z0-9][a-z0-9-]*$) admits these;
        # they are valid ids and still carry the leading PR number.
        for name in ("1234-.added.md", "1234--x.added.md", "9abc.added.md"):
            self.assertIsInstance(assemble.parse_name(name), tuple, name)

    def test_bad_types_and_shapes(self) -> None:
        for name in (
            "1234.feature.md",
            "1234.Added.md",
            "1234.added.txt",
            "1234.md",
            "1234.added",
            "1234.added.extra.md",
        ):
            self.assertIsInstance(assemble.parse_name(name), str, name)

    def test_check_reports_every_problem_and_ignores_readme(self) -> None:
        self.frag("1.added.md", "fine\n")
        self.frag("bad_name.added.md", "x\n")
        self.frag("2.nope.md", "x\n")
        self.frag("3.fixed.md", "\n\n   \n")
        code, out, err = run("check", "--dir", str(self.frag_dir))
        self.assertEqual(code, 1)
        self.assertIn("bad_name.added.md", err)
        self.assertIn("2.nope.md", err)
        self.assertIn("3.fixed.md", err)
        self.assertIn("empty", err)
        self.assertNotIn("README.md", err)
        self.assertIn("3 problem(s)", err)

    def test_check_passes_on_valid_dirs_and_readme_only(self) -> None:
        other = self.root / "other.d"
        other.mkdir()
        (other / "README.md").write_text("x\n")
        self.frag("10.added.md", "ok\n")
        code, _, err = run("check", "--dir", str(self.frag_dir), "--dir", str(other))
        self.assertEqual(code, 0, err)

    def test_check_missing_dir_is_a_problem(self) -> None:
        code, _, err = run("check", "--dir", str(self.root / "nope"))
        self.assertEqual(code, 1)
        self.assertIn("not a directory", err)


class Content(TempDirCase):
    def test_trims_leading_and_trailing_blank_lines(self) -> None:
        self.assertEqual(assemble.trim_content("\n\n  \nfirst\nsecond\n\n\t\n"), ("first", "second"))

    def test_interior_blank_lines_are_kept(self) -> None:
        self.assertEqual(assemble.trim_content("a\n\nb\n"), ("a", "", "b"))

    def test_empty_fragment_fails(self) -> None:
        self.frag("1.added.md", " \n\n")
        with self.assertRaises(assemble.FragmentError) as ctx:
            assemble.load_dir(self.frag_dir)
        self.assertEqual(len(ctx.exception.problems), 1)
        self.assertIn("empty", ctx.exception.problems[0])

    def test_directory_named_like_a_fragment_fails(self) -> None:
        (self.frag_dir / "7.added.md").mkdir()
        with self.assertRaises(assemble.FragmentError) as ctx:
            assemble.load_dir(self.frag_dir)
        self.assertIn("not a regular file", ctx.exception.problems[0])

    def test_non_utf8_and_nul_fragments_fail(self) -> None:
        (self.frag_dir / "1.added.md").write_bytes(b"caf\xe9\n")
        (self.frag_dir / "2.added.md").write_bytes(b"has a \x00 byte\n")
        with self.assertRaises(assemble.FragmentError) as ctx:
            assemble.load_dir(self.frag_dir)
        self.assertEqual(len(ctx.exception.problems), 2)
        self.assertIn("not valid UTF-8", ctx.exception.problems[0])
        self.assertIn("NUL", ctx.exception.problems[1])

    def test_bom_is_stripped(self) -> None:
        (self.frag_dir / "3.added.md").write_text("\ufeffbom first line\n", encoding="utf-8")
        [frag] = assemble.load_dir(self.frag_dir)
        self.assertEqual(frag.lines, ("bom first line",))

    def test_pr_link_not_suppressed_by_a_longer_word_token(self) -> None:
        self.frag("42.added.md", "mentions #42abc only")
        [frag] = assemble.load_dir(self.frag_dir)
        self.assertEqual(assemble.render_bullet(frag, pr_links=True), ["- mentions #42abc only (#42)"])

    def test_fragment_ending_on_a_code_fence_fails(self) -> None:
        self.frag("1.added.md", "Adds `x`:\n\n```py\nx()\n```\n")
        with self.assertRaises(assemble.FragmentError) as ctx:
            assemble.load_dir(self.frag_dir)
        self.assertEqual(len(ctx.exception.problems), 1)
        self.assertIn("end with prose", ctx.exception.problems[0])

    def test_fragment_with_its_own_bullet_marker_fails(self) -> None:
        self.frag("1.added.md", "- already a bullet\n")
        self.frag("2.fixed.md", "  indented first line\n")
        with self.assertRaises(assemble.FragmentError) as ctx:
            assemble.load_dir(self.frag_dir)
        self.assertEqual(len(ctx.exception.problems), 2)
        for problem in ctx.exception.problems:
            self.assertIn("without a leading", problem)


class Rendering(TempDirCase):
    def test_bullet_first_line_and_continuation_indent(self) -> None:
        self.frag("5.added.md", "**Thing** — does a\nthing over two\nlines.\n")
        body = assemble.render_body(assemble.load_dir(self.frag_dir))
        self.assertEqual(
            body,
            "### Added\n\n- **Thing** — does a\n  thing over two\n  lines.\n",
        )

    def test_interior_blank_line_is_not_indented(self) -> None:
        self.frag("5.added.md", "a\n\nb\n")
        body = assemble.render_body(assemble.load_dir(self.frag_dir))
        self.assertEqual(body, "### Added\n\n- a\n\n  b\n")

    def test_section_order_and_omission(self) -> None:
        self.frag("1.security.md", "s")
        self.frag("2.fixed.md", "f")
        self.frag("3.removed.md", "r")
        self.frag("4.deprecated.md", "d")
        self.frag("5.changed.md", "c")
        self.frag("6.added.md", "a")
        self.frag("7.breaking.md", "b")
        body = assemble.render_body(assemble.load_dir(self.frag_dir))
        headings = [l for l in body.splitlines() if l.startswith("### ")]
        self.assertEqual(
            headings,
            ["### BREAKING", "### Added", "### Changed", "### Deprecated",
             "### Removed", "### Fixed", "### Security"],
        )
        # Sections are separated by exactly one blank line and nothing else.
        self.assertEqual(
            body,
            "### BREAKING\n\n- b\n\n### Added\n\n- a\n\n### Changed\n\n- c\n\n"
            "### Deprecated\n\n- d\n\n### Removed\n\n- r\n\n### Fixed\n\n- f\n\n"
            "### Security\n\n- s\n",
        )

    def test_only_present_sections_are_emitted(self) -> None:
        self.frag("1.fixed.md", "f")
        self.frag("2.added.md", "a")
        body = assemble.render_body(assemble.load_dir(self.frag_dir))
        self.assertEqual(body, "### Added\n\n- a\n\n### Fixed\n\n- f\n")

    def test_empty_dir_renders_nothing(self) -> None:
        self.assertEqual(assemble.render_body([]), "")
        code, out, _ = run("render", "--dir", str(self.frag_dir))
        self.assertEqual((code, out), (0, ""))

    def test_sort_numeric_ascending_then_slug_by_string(self) -> None:
        self.frag("100.added.md", "hundred")
        self.frag("20.added.md", "twenty")
        self.frag("20-b.added.md", "twenty-b")
        self.frag("20-a.added.md", "twenty-a")
        self.frag("3.added.md", "three")
        self.frag("zeta.added.md", "zeta")
        self.frag("alpha.added.md", "alpha")
        self.frag("9abc.added.md", "slug starting with a digit")
        body = assemble.render_body(assemble.load_dir(self.frag_dir))
        self.assertEqual(
            body.splitlines()[2:],  # heading, blank line, then the bullets
            ["- three", "- twenty", "- twenty-a", "- twenty-b", "- hundred",
             "- slug starting with a digit", "- alpha", "- zeta"],
        )

    def test_render_cli_fails_on_invalid_fragment(self) -> None:
        self.frag("1.added.md", "ok")
        self.frag("BAD.added.md", "x")
        code, out, err = run("render", "--dir", str(self.frag_dir))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("BAD.added.md", err)


class PrLinks(TempDirCase):
    def test_appends_pr_ref_to_last_line(self) -> None:
        self.frag("42.added.md", "first\nlast")
        body = assemble.render_body(assemble.load_dir(self.frag_dir), pr_links=True)
        self.assertEqual(body, "### Added\n\n- first\n  last (#42)\n")

    def test_slug_suffixed_id_uses_the_number(self) -> None:
        self.frag("42-extra.added.md", "text")
        body = assemble.render_body(assemble.load_dir(self.frag_dir), pr_links=True)
        self.assertEqual(body, "### Added\n\n- text (#42)\n")

    def test_does_not_duplicate_existing_ref(self) -> None:
        self.frag("42.added.md", "already links (#42)")
        self.frag("43.added.md", "mid-text #43 mention\nsecond line")
        body = assemble.render_body(assemble.load_dir(self.frag_dir), pr_links=True)
        self.assertEqual(
            body, "### Added\n\n- already links (#42)\n- mid-text #43 mention\n  second line\n"
        )

    def test_longer_number_does_not_count_as_existing_ref(self) -> None:
        self.frag("42.added.md", "mentions #420 only")
        body = assemble.render_body(assemble.load_dir(self.frag_dir), pr_links=True)
        self.assertEqual(body, "### Added\n\n- mentions #420 only (#42)\n")

    def test_noop_for_slug_only_ids(self) -> None:
        self.frag("some-slug.added.md", "text")
        self.frag("9abc.added.md", "digit-led slug")
        body = assemble.render_body(assemble.load_dir(self.frag_dir), pr_links=True)
        self.assertEqual(body, "### Added\n\n- digit-led slug\n- some-slug\n".replace("some-slug", "text"))

    def test_off_by_default(self) -> None:
        self.frag("42.added.md", "text")
        body = assemble.render_body(assemble.load_dir(self.frag_dir))
        self.assertEqual(body, "### Added\n\n- text\n")
        code, out, _ = run("render", "--dir", str(self.frag_dir), "--pr-links")
        self.assertEqual((code, out), (0, "### Added\n\n- text (#42)\n"))


INTRO = "# Changelog\n\nIntro paragraph.\n\n"
OLD = "## [0.3.2] — 2026-08-06\n\n### Fixed\n- old fix.\n"


class Assemble(TempDirCase):
    def test_inserts_before_first_release_heading(self) -> None:
        cl = self.changelog(INTRO + OLD)
        self.frag("7.added.md", "new thing\nwrapped")
        self.frag("3.fixed.md", "a fix")
        code, out, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "0.4.0", "--date", "2026-09-10", "--pr-links",
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")
        self.assertEqual(
            cl.read_text(),
            INTRO
            + "## [0.4.0] — 2026-09-10\n\n"
            + "### Added\n\n- new thing\n  wrapped (#7)\n\n"
            + "### Fixed\n\n- a fix (#3)\n\n"
            + OLD,
        )

    def test_adds_blank_line_when_heading_follows_text_directly(self) -> None:
        cl = self.changelog("# Changelog\nIntro.\n" + OLD)
        self.frag("1.added.md", "x")
        code, _, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "1.0.0", "--date", "2026-01-01",
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(
            cl.read_text(),
            "# Changelog\nIntro.\n\n## [1.0.0] — 2026-01-01\n\n### Added\n\n- x\n\n" + OLD,
        )

    def test_appends_at_end_when_no_release_heading(self) -> None:
        cl = self.changelog("# Changelog\n\nNothing released yet.\n\n\n")
        self.frag("1.added.md", "x")
        code, _, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "0.1.0", "--date", "2026-01-01",
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(
            cl.read_text(),
            "# Changelog\n\nNothing released yet.\n\n## [0.1.0] — 2026-01-01\n\n### Added\n\n- x\n",
        )

    def test_prerelease_version_accepted_and_bad_version_refused(self) -> None:
        cl = self.changelog(INTRO + OLD)
        self.frag("1.added.md", "x")
        code, _, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "2.0.0-alpha.1", "--date", "2026-01-01", "--dry-run",
        )
        self.assertEqual(code, 0, err)
        code, _, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "2.0", "--dry-run",
        )
        self.assertEqual(code, 1)
        self.assertIn("X.Y.Z", err)

    def test_refuses_duplicate_version(self) -> None:
        cl = self.changelog(INTRO + OLD)
        self.frag("1.added.md", "x")
        code, _, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "0.3.2",
        )
        self.assertEqual(code, 1)
        self.assertIn("already has a heading for 0.3.2", err)
        self.assertEqual(cl.read_text(), INTRO + OLD)
        self.assertTrue((self.frag_dir / "1.added.md").exists())

    def test_duplicate_check_is_exact_not_prefix(self) -> None:
        cl = self.changelog(INTRO + "## [0.3.22] — 2026-08-06\n")
        self.frag("1.added.md", "x")
        code, _, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "0.3.2", "--date", "2026-01-01", "--dry-run",
        )
        self.assertEqual(code, 0, err)

    def test_refuses_when_no_fragments(self) -> None:
        cl = self.changelog(INTRO + OLD)
        code, _, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "0.4.0",
        )
        self.assertEqual(code, 1)
        self.assertIn("no changelog fragments", err)
        self.assertEqual(cl.read_text(), INTRO + OLD)

    def test_refuses_invalid_fragment(self) -> None:
        cl = self.changelog(INTRO + OLD)
        self.frag("1.added.md", "x")
        self.frag("1.bogus.md", "y")
        code, _, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "0.4.0",
        )
        self.assertEqual(code, 1)
        self.assertIn("1.bogus.md", err)
        self.assertTrue((self.frag_dir / "1.added.md").exists())

    def test_bad_date_refused(self) -> None:
        cl = self.changelog(INTRO + OLD)
        self.frag("1.added.md", "x")
        code, _, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "0.4.0", "--date", "yesterday",
        )
        self.assertEqual(code, 1)
        self.assertIn("YYYY-MM-DD", err)

    def test_default_date_is_today_utc(self) -> None:
        cl = self.changelog(INTRO + OLD)
        self.frag("1.added.md", "x")
        code, out, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "0.4.0", "--dry-run",
        )
        self.assertEqual(code, 0, err)
        self.assertIn(f"## [0.4.0] — {assemble._today()}\n", out)

    def test_compare_url_adds_reference_link_above_existing_block(self) -> None:
        cl = self.changelog(INTRO + OLD + "\n[0.3.0]: https://x/compare/a...v0.3.0\n")
        self.frag("1.added.md", "x")
        code, out, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "0.4.0", "--date", "2026-01-01", "--dry-run",
            "--compare-url", "https://x/compare/v{prev}...v{version}",
        )
        self.assertEqual(code, 0, err)
        self.assertTrue(
            out.endswith(
                "[0.4.0]: https://x/compare/v0.3.2...v0.4.0\n"
                "[0.3.0]: https://x/compare/a...v0.3.0\n"
            ),
            out,
        )
        self.assertIn("## [0.4.0] — 2026-01-01", out)

    def test_compare_url_without_existing_block_appends(self) -> None:
        cl = self.changelog(INTRO + OLD)
        self.frag("1.added.md", "x")
        code, out, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "0.4.0", "--date", "2026-01-01", "--dry-run",
            "--compare-url", "https://x/compare/v{prev}...v{version}",
        )
        self.assertEqual(code, 0, err)
        self.assertTrue(out.endswith("- old fix.\n\n[0.4.0]: https://x/compare/v0.3.2...v0.4.0\n"), out)

    def test_compare_url_prev_without_earlier_release_is_refused(self) -> None:
        cl = self.changelog(INTRO)
        self.frag("1.added.md", "x")
        code, _, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "0.1.0", "--date", "2026-01-01", "--dry-run",
            "--compare-url", "https://x/compare/v{prev}...v{version}",
        )
        self.assertEqual(code, 1)
        self.assertIn("no earlier release", err)
        self.assertTrue(self.frag_dir.joinpath("1.added.md").exists())

    def test_compare_url_must_mention_version(self) -> None:
        cl = self.changelog(INTRO + OLD)
        self.frag("1.added.md", "x")
        code, _, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "0.4.0", "--dry-run", "--compare-url", "https://x/",
        )
        self.assertEqual(code, 1)
        self.assertIn("{version}", err)

    def test_bare_unreleased_heading_is_refused_too(self) -> None:
        cl = self.changelog(INTRO + "## Unreleased\n\n- stray\n\n" + OLD)
        self.frag("1.added.md", "x")
        code, _, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "0.4.0", "--date", "2026-01-01",
        )
        self.assertEqual(code, 1)
        self.assertIn("Unreleased", err)

    def test_missing_changelog_is_refused_with_fragments_intact(self) -> None:
        frag = self.frag("1.added.md", "x")
        code, _, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog",
            str(self.frag_dir / "nope" / "CHANGELOG.md"), "--version", "0.4.0",
        )
        self.assertEqual(code, 1)
        self.assertIn("does not exist", err)
        self.assertTrue(frag.exists())

    def test_write_does_not_follow_a_planted_symlink_at_the_old_tmp_path(self) -> None:
        cl = self.changelog(INTRO + OLD)
        victim = cl.parent / "victim.txt"
        victim.write_text("untouched\n")
        (cl.parent / (cl.name + ".tmp")).symlink_to(victim)  # the old, predictable temp path
        self.frag("1.added.md", "x")
        code, _, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "0.4.0", "--date", "2026-01-01",
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(victim.read_text(), "untouched\n")
        self.assertIn("## [0.4.0]", cl.read_text())
        # No temp file left behind in the changelog's directory.
        self.assertEqual(sorted(p.name for p in cl.parent.iterdir() if p.name.endswith(".tmp")), [cl.name + ".tmp"])

    def test_prerelease_version_is_accepted(self) -> None:
        cl = self.changelog(INTRO + OLD)
        self.frag("1.added.md", "x")
        code, out, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "0.11.0-alpha.1", "--date", "2026-01-01", "--dry-run",
        )
        self.assertEqual(code, 0, err)
        self.assertIn("## [0.11.0-alpha.1] — 2026-01-01", out)

    def test_unreleased_section_is_refused(self) -> None:
        cl = self.changelog(INTRO + "## [Unreleased]\n\n### Added\n\n- stray\n\n" + OLD)
        frag = self.frag("1.added.md", "x")
        code, _, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "0.4.0", "--date", "2026-01-01",
        )
        self.assertEqual(code, 1)
        self.assertIn("[Unreleased]", err)
        self.assertTrue(frag.exists())

    def test_dry_run_prints_and_deletes_nothing(self) -> None:
        original = INTRO + OLD
        cl = self.changelog(original)
        frag = self.frag("1.added.md", "x")
        code, out, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "0.4.0", "--date", "2026-01-01", "--dry-run",
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out, INTRO + "## [0.4.0] — 2026-01-01\n\n### Added\n\n- x\n\n" + OLD)
        self.assertEqual(cl.read_text(), original)
        self.assertTrue(frag.exists())
        self.assertTrue((self.frag_dir / "README.md").exists())

    def test_real_run_deletes_fragments_but_keeps_readme(self) -> None:
        cl = self.changelog(INTRO + OLD)
        a = self.frag("1.added.md", "x")
        b = self.frag("2-slug.fixed.md", "y")
        code, _, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "0.4.0", "--date", "2026-01-01",
        )
        self.assertEqual(code, 0, err)
        self.assertFalse(a.exists())
        self.assertFalse(b.exists())
        self.assertEqual(sorted(p.name for p in self.frag_dir.iterdir()), ["README.md"])
        self.assertIn("2 fragment(s)", err)
        # A second release from the now-empty directory is refused.
        code, _, err = run(
            "assemble", "--dir", str(self.frag_dir), "--changelog", str(cl),
            "--version", "0.4.1",
        )
        self.assertEqual(code, 1)
        self.assertIn("no changelog fragments", err)


if __name__ == "__main__":
    unittest.main()
