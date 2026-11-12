# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Public command line: inspect first, refuse safely, then apply planned edits."""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import traceback
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Literal, NoReturn, TypedDict

from . import __version__
from .models import Item, Mode, TransformOptions, load_mapping
from .project import ProjectResult, preflight
from .project_progress import slow_progress
from .safety import GitSafety, git_safety
from .safety import _dirty as _dirty
from .v0_version import Warning, ancestor_declarations, check_v0_version
from .writes import write_outputs

Reason = Literal[
    "dirty-tree",
    "custom-api-url",
    "no-daytona",
    "bad-arguments",
    "local-sdk-module",
    "newer-v0",
]


class RefusalReport(TypedDict):
    reason: Reason
    message: str


DependencyEntry = TypedDict("DependencyEntry", {"file": str, "from": str, "to": str})


class ReportBase(TypedDict):
    tool: str
    version: str
    language: str
    mode: Mode
    root: str
    dryRun: bool
    changedFiles: list[str]
    dependencies: list[DependencyEntry]
    items: list[Item]
    nextSteps: list[str]


class Report(ReportBase, total=False):
    refused: RefusalReport
    warnings: list[Warning]


@dataclass(frozen=True)
class Refused(Exception):
    reason: Reason
    message: str

    def __str__(self) -> str:
        return self.message


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise Refused("bad-arguments", message)


def _parser() -> ArgumentParser:
    # stdlib parsing keeps the published package's only dependencies LibCST/tomlkit.
    parser = ArgumentParser(
        # Flags are spelled out, as the npm tool requires.
        allow_abbrev=False,
        prog="daytona-upgrade",
        description="Upgrade 0.x code to SDK 2.x on the v0 namespace and bump the dependency.",
    )
    parser.add_argument("path", nargs="?", default=".")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--suggest-v2",
        action="store_true",
        help="Write daytona-v2: suggestion comments; no code or dependency change",
    )
    modes.add_argument(
        "--remove-suggestions",
        action="store_true",
        help="Remove daytona-v2: suggestion comments",
    )
    for flag, help_text in (
        ("dry", "Show the diff, write nothing (skips the Git check)"),
        (
            "print",
            "Print changed files to stdout instead of writing them (skips the Git check)",
        ),
        ("json", "Print the report as JSON (with --suggest-v2, writes nothing)"),
        ("force", "Run on a Git tree with uncommitted changes"),
        (
            "drop-api-url",
            "Accept API URLs the tool cannot verify or that come from the environment, asserting they point at Daytona Cloud",
        ),
        ("strict", "Exit 1 when manual items remain"),
        (
            "allow-newer-v0",
            "Migrate a project on a newer 0.x minor than the 0.x release SDK 2.x runs as .v0",
        ),
    ):
        parser.add_argument("--" + flag, action="store_true", help=help_text)
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Do not prompt (automatic when stdin is not a TTY)",
    )
    parser.add_argument("-v", "--version", action="version", version=__version__)
    return parser


def _emit(
    report: Report, result: ProjectResult | None, args: argparse.Namespace
) -> None:
    if args.json:
        print(json.dumps(report, indent=2))
        return
    if "refused" in report:
        print(f"Refused: {report['refused']['message']}", file=sys.stderr)
    if result is not None:
        for change in result.changes:
            path = change.file.relative
            if args.dry:
                for line in difflib.unified_diff(
                    change.file.source.splitlines(keepends=True),
                    change.source.splitlines(keepends=True),
                    fromfile="a/" + path,
                    tofile="b/" + path,
                ):
                    print(
                        line,
                        end=""
                        if line.endswith("\n")
                        else "\n\\ No newline at end of file\n",
                    )
            elif args.print:
                print(
                    f"--- {path} ---\n{change.source}",
                    end="" if change.source.endswith("\n") else "\n",
                )
            else:
                print(f"Updated {path}")
    for item in report["items"]:
        print(
            f"{item['file']}:{item['line']}:{item['column']}: {item['kind']}: {item['message']}"
        )
    for warning in report.get("warnings", []):
        print(f"{warning['file']}: warning: {warning['message']}")
    for step in report["nextSteps"]:
        print(step)


def main(argv: list[str] | None = None) -> int:
    """Run the real CLI, returning 0 (done), 1 (error/manual), or 2 (refused)."""
    arguments = sys.argv[1:] if argv is None else argv
    args = argparse.Namespace(json="--json" in arguments, dry=False, print=False)
    report = Report(
        tool="daytona-upgrade",
        version=__version__,
        language="py",
        mode="upgrade",
        root=str(Path.cwd()),
        dryRun=False,
        changedFiles=[],
        dependencies=[],
        items=[],
        nextSteps=[],
    )
    result: ProjectResult | None = None
    try:
        args = _parser().parse_args(arguments)
        root = Path(args.path).expanduser().resolve()
        mode: Mode = (
            "suggest-v2"
            if args.suggest_v2
            else "remove-suggestions"
            if args.remove_suggestions
            else "upgrade"
        )
        readonly = args.dry or args.print or (args.json and args.suggest_v2)
        report.update(mode=mode, root=str(root), dryRun=readonly)
        if not root.exists():
            raise Refused("bad-arguments", f"Path does not exist: {root}")
        safety = GitSafety() if readonly or args.force else git_safety(root)
        if safety.note:
            report["nextSteps"].append(safety.note)
        if safety.dirty and not args.force:
            raise Refused(
                "dirty-tree",
                "Git tree has uncommitted changes. Commit or stash them, or run with --force.",
            )
        report_progress = partial(print, file=sys.stderr, flush=True)
        mapping = load_mapping()
        with slow_progress(report_progress):
            result = preflight(
                root,
                TransformOptions(mapping, mode, args.drop_api_url),
                report_progress,
            )
        if result.local_module:
            raise Refused("local-sdk-module", result.local_module.message())
        report["items"] = list(result.items)
        # A custom API URL refuses (exit 2) even when files are also unsupported.
        if any(item["kind"] == "custom-api-url" for item in result.items):
            from .config_messages import url_refusal

            message, report["nextSteps"] = url_refusal(result.items)
            raise Refused("custom-api-url", message)
        # Then the 0.x version (refuses exit 2 before unsupported files exit 1).
        if mode == "upgrade" and result.found:
            base = root.parent if root.is_file() else root
            v0 = check_v0_version(
                base,
                # A file root starts at its own directory.
                result.declared or ancestor_declarations(root, mapping),
                mapping.v0_version,
                args.allow_newer_v0,
            )
            if v0.warnings:
                report["warnings"] = list(v0.warnings)
            if v0.refusal is not None:
                raise Refused("newer-v0", v0.refusal)
        if any(item["kind"] == "unsupported-file" for item in result.items):
            _emit(report, None, args)
            return 1
        if not result.found:
            raise Refused("no-daytona", "No Daytona dependency or code found.")
        report["changedFiles"] = [change.file.relative for change in result.changes]
        report["dependencies"] = [
            {"file": entry.file, "from": entry.before, "to": entry.after}
            for entry in result.dependencies
        ]
        if mode == "upgrade" and result.dependencies:
            from .manifests import next_step

            # The requirement written, when every rewrite wrote the same one
            # (a kept or newer pin is never named as a downgrade target).
            written = {
                entry.after
                for entry in result.dependencies
                if entry.after.startswith(mapping.package_new)
            }
            requirement = next(iter(written)) if len(written) == 1 else None
            report["nextSteps"].append(
                next_step(root.parent if root.is_file() else root, requirement)
            )
        manual = any(
            item["kind"] not in {"suggestion", "workflow"}
            and not (item["kind"] == "types-unresolved" and item["file"] == ".")
            for item in result.items
        )
        if not readonly:
            payloads = [
                (change.file.path, change.source.encode(change.file.encoding))
                for change in result.changes
            ]
            if result.changes and sys.stdin.isatty() and not args.yes:
                print(
                    f"Update {len(result.changes)} file(s)? [y/N] ",
                    file=sys.stderr,
                    end="",
                    flush=True,
                )
                answer = input()
                if answer.strip().lower() not in {"y", "yes"}:
                    report["dryRun"] = True
                    _emit(report, None, args)
                    return 1 if args.strict and manual else 0
            write_outputs(payloads, root.parent if root.is_file() else root)
        _emit(report, result, args)
        return 1 if args.strict and manual else 0
    except Refused as error:
        report["refused"] = RefusalReport(reason=error.reason, message=error.message)
        _emit(report, None, args)
        return 2
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 1
    except (OSError, UnicodeError, ValueError, SyntaxError) as error:
        print(f"daytona-upgrade: {error}", file=sys.stderr)
        if args.json:
            report["nextSteps"].append(f"daytona-upgrade: {error}")
            _emit(report, None, args)
        return 1
    except Exception as error:  # noqa: BLE001 -- final CLI boundary prints traceback and returns the specified exit 1.
        traceback.print_exc()
        if args.json:
            report["nextSteps"].append(f"daytona-upgrade: {error}")
            _emit(report, None, args)
        return 1
