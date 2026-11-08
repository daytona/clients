# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for scripts/breaking/breaking_changes.py (stdlib unittest, temporary git repositories).

Run: python3 -m unittest discover -s scripts/breaking -v
The OpenAPI cases need oasdiff (OASDIFF or PATH; scripts/breaking/install-oasdiff.sh)
and are skipped without it.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import breaking_changes as bc  # noqa: E402

OASDIFF = os.environ.get("OASDIFF") or shutil.which("oasdiff")


class CompatibleCall(unittest.TestCase):
    def test_typescript_trailing_optional_parameter(self) -> None:
        self.assertTrue(bc.compatible_change("method (a: string): void", "method (a: string, b?: number): void"))

    def test_typescript_required_parameter_breaks(self) -> None:
        self.assertFalse(bc.compatible_change("method (a: string): void", "method (a: string, b: number): void"))

    def test_typescript_parameter_became_optional(self) -> None:
        self.assertTrue(bc.compatible_change("method (a: string): void", "method (a?: string): void"))

    def test_return_type_change_breaks(self) -> None:
        self.assertFalse(bc.compatible_change("method (): Promise<A>", "method (): Promise<B>"))

    def test_python_keyword_only_parameter_with_default(self) -> None:
        self.assertTrue(
            bc.compatible_change(
                "async def (x: int, *, a: str = 'v') -> None",
                "async def (x: int, *, b: int | None = None, a: str = 'v') -> None",
            )
        )

    def test_python_first_keyword_only_parameter(self) -> None:
        self.assertTrue(bc.compatible_change("def (x: int) -> None", "def (x: int, *, y: int = 1) -> None"))

    def test_python_positional_insert_shifts_callers(self) -> None:
        self.assertFalse(bc.compatible_change("def (x: int, y: int = 0) -> None", "def (x: int, z: int = 1, y: int = 0) -> None"))

    def test_python_parameter_made_keyword_only_breaks(self) -> None:
        self.assertFalse(bc.compatible_change("def (x: int, y: int = 0) -> None", "def (x: int, *, y: int = 0) -> None"))

    def test_python_parameter_gained_default(self) -> None:
        self.assertTrue(bc.compatible_change("def (x: int) -> None", "def (x: int = 0) -> None"))

    def test_parameter_removed_breaks(self) -> None:
        self.assertFalse(bc.compatible_change("def (x: int, y: int = 0) -> None", "def (x: int) -> None"))

    def test_nested_brackets_and_arrows(self) -> None:
        self.assertTrue(
            bc.compatible_change(
                "method (cb: (e: Map<string, number>) => void, o?: { a: string; b?: number; }): Promise<void>",
                "method (cb: (e: Map<string, number>) => void, o?: { a: string; b?: number; }, p?: string): Promise<void>",
            )
        )

    def test_union_alias_gained_member(self) -> None:
        self.assertTrue(bc.compatible_change("type = 'a' | 'b'", "type = 'a' | 'b' | 'c'"))
        self.assertTrue(bc.compatible_change("type = Literal['a', 'b']", "type = Literal['a', 'b', 'c']"))
        self.assertFalse(bc.compatible_change("type = 'a' | 'b'", "type = 'a' | 'c'"))

    def test_class_gained_base(self) -> None:
        self.assertTrue(bc.compatible_change("class (A)", "class (A, B)"))
        self.assertFalse(bc.compatible_change("class (A, B)", "class (B)"))


class CompareSurface(unittest.TestCase):
    OLD = "\n".join(
        [
            "# header",
            "Client: class",
            "Client.close: method (): void",
            "Client.constructor: internal-constructor (a: string)",
            "Client.run: method (a: string): void",
            "Gone: class",
            "Gone.x: property string",
            "State: type = union",
            "State: union-member 'a'",
            "State: union-member 'b'",
            "daytona sandbox: alias sb",
            "daytona sandbox create --name: flag string default=\"\"",
            "$DAYTONA_API_KEY: env",
            'report.mode: field string required',
            'report.mode: value "remove-suggestions"',
            'report.mode: value "upgrade"',
        ]
    )

    def compare(self, new: str) -> bc.Report:
        report = bc.Report()
        bc.compare_surface("s.api.txt", self.OLD, new, report)
        return report

    def test_identical(self) -> None:
        report = self.compare(self.OLD)
        self.assertEqual(report.breaking, [])
        self.assertEqual(report.notices, [])

    def test_additions_pass(self) -> None:
        report = self.compare(self.OLD + "\nNew: class\nState: union-member 'c'\ndaytona sandbox: alias sbx\n$X: env")
        self.assertEqual(report.breaking, [])

    def test_removed_class_is_one_finding(self) -> None:
        new = "\n".join(line for line in self.OLD.splitlines() if not line.startswith("Gone"))
        report = self.compare(new)
        self.assertEqual(report.breaking, [bc.snapshot_finding("s.api.txt", "Gone: class")])

    def test_removed_members_flag_alias_env(self) -> None:
        for line in (
            "State: union-member 'b'",
            "daytona sandbox: alias sb",
            "daytona sandbox create --name: flag string default=\"\"",
            "$DAYTONA_API_KEY: env",
            'report.mode: value "remove-suggestions"',
        ):
            with self.subTest(line=line):
                report = self.compare(self.OLD.replace(line + "\n", "").replace("\n" + line, ""))
                self.assertEqual(report.breaking, [bc.snapshot_finding("s.api.txt", line)])

    def test_renamed_flag_breaks(self) -> None:
        report = self.compare(self.OLD.replace("create --name:", "create --title:"))
        self.assertEqual(report.breaking, [bc.snapshot_finding("s.api.txt", 'daytona sandbox create --name: flag string default=""')])

    def test_changed_signature_breaks_and_compatible_one_does_not(self) -> None:
        report = self.compare(self.OLD.replace("run: method (a: string)", "run: method (a: number)"))
        self.assertEqual(
            report.breaking,
            [bc.snapshot_finding("s.api.txt", "Client.run: method (a: string): void", ["Client.run: method (a: number): void"])],
        )
        report = self.compare(self.OLD.replace("run: method (a: string)", "run: method (a: string, b?: number)"))
        self.assertEqual(report.breaking, [])
        self.assertEqual(len(report.notices), 1)

    def test_changed_field_type_breaks(self) -> None:
        report = self.compare(self.OLD.replace("report.mode: field string required", "report.mode: field string optional"))
        self.assertEqual(len(report.breaking), 1)

    def test_internal_constructor_is_a_notice(self) -> None:
        report = self.compare(self.OLD.replace("internal-constructor (a: string)", "internal-constructor (a: number)"))
        self.assertEqual(report.breaking, [])
        self.assertIn("not public API", report.notices[0])

    def test_removed_snapshot(self) -> None:
        report = bc.Report()
        bc.compare_surface("s.api.txt", self.OLD, None, report)
        self.assertEqual(len(report.breaking), 1)


class ContractFiles(unittest.TestCase):
    def test_catalog(self) -> None:
        old = {"codes": [{"code": "A", "status": 409, "extras": ["x"]}], "generic": [{"code": "G", "status": 400}]}
        same_plus = {
            "codes": [{"code": "A", "status": 409, "extras": ["x", "y"]}, {"code": "B", "status": 404}],
            "generic": [{"code": "G", "status": 400}],
        }
        self.assertEqual(bc.catalog_findings("c.json", json.dumps(old), json.dumps(same_plus)), set())
        broken = {"codes": [{"code": "A", "status": 410, "extras": []}], "generic": []}
        found = bc.catalog_findings("c.json", json.dumps(old), json.dumps(broken))
        self.assertEqual(len(found), 3, found)

    def test_catalog_source(self) -> None:
        old = {"source": "DAYTONA_PROXY", "codes": [], "generic": []}
        found = bc.catalog_findings("c.json", json.dumps(old), json.dumps({**old, "source": "DAYTONA_EDGE"}))
        self.assertEqual([dict(f.fields)["rule"] for f in found], ["source-changed"])

    EVENTS = "contract/sandbox-events-protocol.json"

    def test_json(self) -> None:
        old = {
            "$comment": "x",
            "subprotocol": "p.v1",
            "frames": [{"name": "a", "semantics": "some prose"}],
            "codes": ["X", "Y"],
        }
        grown = {**old, "frames": [{"name": "a", "semantics": "other prose"}, {"name": "b"}], "codes": ["X", "Y", "Z"]}
        self.assertEqual(bc.json_findings(self.EVENTS, json.dumps(old), json.dumps(grown)), set())
        reordered = {**old, "pairs": [{"b": 2}, {"a": 1}]}
        self.assertEqual(
            bc.json_findings("e.json", json.dumps({**old, "pairs": [{"a": 1}, {"b": 2}]}), json.dumps(reordered)), set()
        )
        broken = {"subprotocol": "p.v2", "frames": [], "codes": ["X"]}
        found = bc.json_findings(self.EVENTS, json.dumps(old), json.dumps(broken))
        self.assertEqual(len(found), 3, found)

    def test_json_strings_outside_documentation_are_identifiers(self) -> None:
        old = {"ticket": {"upgrade": {"headers": {"Sec-WebSocket-Protocol": "p.v1, p.ticket"}, "region": "the region"}}}
        reworded = {"ticket": {"upgrade": {"headers": {"Sec-WebSocket-Protocol": "p.v1, p.ticket"}, "region": "a region"}}}
        self.assertEqual(bc.json_findings(self.EVENTS, json.dumps(old), json.dumps(reworded)), set())
        renamed = {"ticket": {"upgrade": {"headers": {"Sec-WebSocket-Protocol": "p.v2, p.ticket"}, "region": "the region"}}}
        found = bc.json_findings(self.EVENTS, json.dumps(old), json.dumps(renamed))
        self.assertEqual([dict(f.fields)["at"] for f in found], ["/ticket/upgrade/headers/Sec-WebSocket-Protocol"])
        # Another contract file has no documentation fields: prose there is compared too.
        self.assertEqual(len(bc.json_findings("contract/other.json", json.dumps(old), json.dumps(reworded))), 1)

    def test_json_removed_empty_container(self) -> None:
        old = {"limits": {}, "codes": [], "kept": 1}
        found = bc.json_findings(self.EVENTS, json.dumps(old), json.dumps({"kept": 1}))
        self.assertEqual(sorted(dict(f.fields)["at"] for f in found), ["/codes", "/limits"])

    def test_open_enums_become_extensible(self) -> None:
        spec = {"a": {"enum": ["x"], "x-daytona-open-enum": True}, "b": {"enum": ["y"], "x-daytona-exact-enum": "p"}}
        out = bc.open_enums_extensible(spec)
        self.assertEqual(out["a"], {"x-extensible-enum": ["x"], "x-daytona-open-enum": True})
        self.assertEqual(out["b"]["enum"], ["y"])


def spec(enum: list[str], paths: tuple[str, ...] = ("/a", "/b"), open_enum: bool = True) -> str:
    schema: dict[str, object] = {"type": "string", "enum": enum}
    if open_enum:
        schema["x-daytona-open-enum"] = True
    ok = {"description": "ok", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/S"}}}}
    return json.dumps(
        {
            "openapi": "3.1.0",
            "info": {"title": "t", "version": "1"},
            "paths": {p: {"get": {"operationId": p.strip("/"), "responses": {"200": ok}}} for p in paths},
            "components": {"schemas": {"S": schema}},
        }
    )


@unittest.skipUnless(OASDIFF, "oasdiff not installed (scripts/breaking/install-oasdiff.sh)")
class OpenAPI(unittest.TestCase):
    def test_open_enum_may_grow_closed_may_not(self) -> None:
        errors, _ = bc.oasdiff_findings(str(OASDIFF), "x.json", spec(["a"]), spec(["a", "b"]))
        self.assertEqual(errors, set())
        errors, _ = bc.oasdiff_findings(str(OASDIFF), "x.json", spec(["a"], open_enum=False), spec(["a", "b"], open_enum=False))
        self.assertTrue(errors)

    def test_removed_request_parameter_is_a_finding(self) -> None:
        def with_param(params: list[str]) -> str:
            doc = json.loads(spec(["a"]))
            doc["paths"]["/a"]["get"]["parameters"] = [
                {"name": n, "in": "query", "required": False, "schema": {"type": "string"}} for n in params
            ]
            return json.dumps(doc)

        errors, _ = bc.oasdiff_findings(str(OASDIFF), "x.json", with_param(["q", "r"]), with_param(["q"]))
        self.assertEqual([dict(f.fields)["rule"] for f in errors], ["request-parameter-removed"])

    def test_removed_path_is_an_error(self) -> None:
        errors, _ = bc.oasdiff_findings(str(OASDIFF), "x.json", spec(["a"]), spec(["a"], paths=("/a",)))
        self.assertEqual(len(errors), 1)
        self.assertEqual(dict(next(iter(errors)).fields)["at"], "GET /b")


class RepoCase(unittest.TestCase):
    """A temporary repository with a CLI snapshot, a catalog and changelog.d."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")
        self.write("cli/api-surface/daytona.api.txt", "daytona: command\ndaytona --yes: flag bool default=\"false\"\n")
        self.write("contract/proxy-errors.json", json.dumps({"codes": [{"code": "A", "status": 409}], "generic": []}))
        (self.root / "changelog.d").mkdir()
        self.write("changelog.d/README.md", "fragments\n")
        self.commit("release")

    def git(self, *args: str) -> str:
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True, env=env).stdout

    def write(self, path: str, text: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", message)

    def run_check(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        code = bc.main(["--root", str(self.root), *args], out=out)
        return code, out.getvalue()



class Repository(RepoCase):
    """End to end in a temporary repository with release tags."""

    def test_no_stable_release(self) -> None:
        self.git("tag", "v2.0.0-rc.2")
        code, out = self.run_check()
        self.assertEqual(code, 0)
        self.assertIn("no stable release yet", out)

    def test_latest_stable_tag(self) -> None:
        for tag in ("v0.230.0", "v2.0.0", "v2.1.0-rc.1", "v2.0.10", "v2.0.9"):
            self.git("tag", tag)
        self.assertEqual(bc.latest_stable_tag(self.root), "v2.0.10")
        # A vX.Y.Z tag whose GitHub release is flagged pre-release is not stable.
        self.assertEqual(bc.latest_stable_tag(self.root, skip={"v2.0.10"}), "v2.0.9")

    def test_prerelease_tags_read_release_flags(self) -> None:
        pages = [
            [{"tag_name": f"v2.0.{i}", "prerelease": False, "draft": False} for i in range(99)]
            + [{"tag_name": "v2.1.0", "prerelease": True, "draft": False}],
            [{"tag_name": "v2.2.0", "prerelease": False, "draft": True}, {"tag_name": "v2.3.0"}],
        ]
        seen: list[str] = []

        class Response(io.BytesIO):
            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_: object) -> None:
                return None

        def fake_urlopen(request: object, timeout: float) -> Response:
            seen.append(request.full_url)  # type: ignore[attr-defined]
            self.assertEqual(request.get_header("Authorization"), "Bearer t")  # type: ignore[attr-defined]
            return Response(json.dumps(pages[len(seen) - 1]).encode())

        original = bc.urllib.request.urlopen
        bc.urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            self.assertEqual(bc.prerelease_tags("o/r", "t"), {"v2.1.0", "v2.2.0"})
        finally:
            bc.urllib.request.urlopen = original
        self.assertEqual(len(seen), 2)
        self.assertIn("page=2", seen[1])

    def test_breaking_change_needs_a_fragment(self) -> None:
        self.git("tag", "v2.0.0")
        self.write("cli/api-surface/daytona.api.txt", "daytona: command\ndaytona --assume-yes: flag bool default=\"false\"\n")
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn('old: daytona --yes: flag bool default="false"', out)
        self.write("changelog.d/flag-rename.breaking.md", "**Renamed** `--yes`.\n")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("changelog.d/flag-rename.breaking.md", out)

    def test_unknown_base_is_an_error(self) -> None:
        self.git("tag", "v2.0.0")
        with self.assertRaises(SystemExit) as raised:
            self.run_check("--base", "no-such-branch")
        self.assertIn("not a commit", str(raised.exception))

    def test_catalog_change_needs_a_fragment(self) -> None:
        self.git("tag", "v2.0.0")
        self.write("contract/proxy-errors.json", json.dumps({"codes": [], "generic": []}))
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("rule: code-removed", out)

    def test_fetch_finds_the_tag_on_a_remote(self) -> None:
        self.git("tag", "v2.0.0")
        self.write("cli/api-surface/daytona.api.txt", "daytona: command\n")
        self.commit("break")
        clone = self.root / "clone"
        self.git("clone", "-q", "--depth=1", "--no-tags", self.root.as_uri(), str(clone))
        self.assertEqual(subprocess.run(["git", "tag"], cwd=clone, capture_output=True, text=True).stdout, "")
        out = io.StringIO()
        code = bc.main(["--root", str(clone), "--fetch", "origin"], out=out)
        self.assertEqual(code, 1, out.getvalue())
        self.assertIn('old: daytona --yes: flag bool default="false"', out.getvalue())

    def test_base_counts_only_new_breaks(self) -> None:
        self.git("tag", "v2.0.0")
        # Already on the base branch, announced there.
        self.write("cli/api-surface/daytona.api.txt", "daytona: command\n")
        self.write("changelog.d/old.breaking.md", "**Removed** `--yes`.\n")
        self.commit("break, announced")
        base = self.git("rev-parse", "HEAD").strip()
        code, out = self.run_check("--base", base)
        self.assertEqual(code, 0, out)
        self.assertIn("already on the base branch", out)
        # A second break on top needs its own fragment.
        self.write("contract/proxy-errors.json", json.dumps({"codes": [], "generic": []}))
        code, out = self.run_check("--base", base)
        self.assertEqual(code, 1, out)
        # Renaming the earlier fragment announces nothing new.
        (self.root / "changelog.d/old.breaking.md").rename(self.root / "changelog.d/renamed.breaking.md")
        code, out = self.run_check("--base", base)
        self.assertEqual(code, 1, out)
        self.write("changelog.d/new.breaking.md", "**Removed** code A.\n")
        code, out = self.run_check("--base", base)
        self.assertEqual(code, 0, out)


class Acknowledgments(RepoCase):
    RENAME = """
- area: cli/api-surface/daytona.api.txt
  old: daytona --yes: flag bool default="false"
  new: daytona --yes: flag bool -y default="false"
  reason: only a shorthand was added
"""

    def acknowledge(self, text: str) -> None:
        self.write(bc.ACKNOWLEDGMENTS, "# header\n" + text)

    def shorthand(self, short: str = "-y") -> None:
        self.write("cli/api-surface/daytona.api.txt", f'daytona: command\ndaytona --yes: flag bool {short} default="false"\n')

    def test_acknowledged_finding_passes_in_push_and_pr_mode(self) -> None:
        self.git("tag", "v2.0.0")
        base = self.git("rev-parse", "HEAD").strip()
        self.shorthand()
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.acknowledge(self.RENAME)
        for args in ((), ("--base", base)):
            with self.subTest(args=args):
                code, out = self.run_check(*args)
                self.assertEqual(code, 0, out)
                self.assertIn("acknowledged as compatible", out)
                self.assertIn("reason: only a shorthand was added", out)
                self.assertIn("No breaking change", out)
        # Push mode on the merged result: the base now carries it.
        self.commit("merged")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)

    def test_a_different_change_to_the_same_declaration_still_fails(self) -> None:
        self.git("tag", "v2.0.0")
        self.acknowledge(self.RENAME)
        self.shorthand("-Y")
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("new: daytona --yes: flag bool -Y", out)
        self.assertIn("matches no finding (stale", out)

    def test_contract_finding_is_acknowledged_by_rule_and_location(self) -> None:
        self.git("tag", "v2.0.0")
        self.write("contract/proxy-errors.json", json.dumps({"codes": [{"code": "A", "status": 410}], "generic": []}))
        self.acknowledge(
            "- area: contract/proxy-errors.json\n  rule: status-changed\n  at: codes:A\n  text: 409 -> 410\n"
            "  reason: the server never returned 409 for A\n"
        )
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.write("contract/proxy-errors.json", json.dumps({"codes": [{"code": "A", "status": 400}], "generic": []}))
        code, out = self.run_check()
        self.assertEqual(code, 1, out)

    def test_stale_entry_is_only_a_notice(self) -> None:
        self.git("tag", "v2.0.0")
        self.acknowledge(self.RENAME)
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("matches no finding (stale", out)

    def test_malformed_entries_fail(self) -> None:
        cases = {
            "no reason": self.RENAME.replace("  reason: only a shorthand was added\n", ""),
            "empty reason": self.RENAME.replace("only a shorthand was added", " "),
            "unknown key": self.RENAME.replace("  reason:", "  why: x\n  reason:"),
            "no area first": self.RENAME.replace("- area: cli/api-surface/daytona.api.txt\n  old:", "- old:"),
            "snapshot without new": self.RENAME.replace('  new: daytona --yes: flag bool -y default="false"\n', ""),
            "mixed kinds": self.RENAME.replace("  reason:", "  rule: x\n  reason:"),
            "duplicate": self.RENAME + self.RENAME,
            "stray line": "area: x\n",
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                self.acknowledge(text)
                with self.assertRaises(SystemExit) as raised:
                    self.run_check()
                self.assertIn("compatible-changes.txt:", str(raised.exception))

    def test_malformed_file_fails_before_a_release_exists(self) -> None:
        self.acknowledge("- area: x\n  old: y\n")
        with self.assertRaises(SystemExit):
            self.run_check()


if __name__ == "__main__":
    unittest.main()
