# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Smoke-test 0.x SDK examples without executing SDK workloads."""

from __future__ import annotations

import ast
import os
import shutil
from pathlib import Path
from typing import Final

import pytest

from daytona_upgrade import cli
from daytona_upgrade.flow import Flow, SourceContext, build_index
from daytona_upgrade.models import load_mapping
from daytona_upgrade.resolution import Resolver

EXAMPLES: Final = os.environ.get("DAYTONA_UPGRADE_V0_EXAMPLES")
SDK_EXAMPLES: Final = Path(EXAMPLES) if EXAMPLES else Path()
pytestmark = pytest.mark.skipif(
    not EXAMPLES,
    reason="set DAYTONA_UPGRADE_V0_EXAMPLES to the examples directory of a daytona/clients checkout",
)


@pytest.mark.parametrize("directory", ["python", "jupyter"])
def test_sdk_examples_when_migrated_twice(directory: str, tmp_path: Path) -> None:
    # Given an isolated copy of the 0.x SDK example corpus.
    examples = SDK_EXAMPLES / directory
    if not examples.is_dir():
        pytest.skip(f"0.x SDK examples unavailable: {examples}")
    project = tmp_path / directory
    shutil.copytree(examples, project)
    (project / "requirements.txt").write_text("daytona==0.224.0\n", encoding="utf-8")
    # When upgrading twice through the real public command.
    first = cli.main([str(project), "--force", "--yes"])
    before = {
        path.relative_to(project): path.read_bytes()
        for path in project.rglob("*")
        if path.is_file()
    }
    second = cli.main([str(project), "--force", "--yes"])
    # Then neither pass crashes and the second pass changes no bytes.
    assert first == second == 0
    assert {
        path.relative_to(project): path.read_bytes()
        for path in project.rglob("*")
        if path.is_file()
    } == before


def test_python_examples_when_proven_create_calls_are_migrated(tmp_path: Path) -> None:
    # Given the 0.x SDK Python corpus with its full cross-module graph.
    examples = SDK_EXAMPLES / "python"
    if not examples.is_dir():
        pytest.skip(f"0.x SDK examples unavailable: {examples}")
    project = tmp_path / "python"
    shutil.copytree(examples, project)
    (project / "requirements.txt").write_text("daytona==0.224.0\n", encoding="utf-8")
    # When running the actual project migration.
    status = cli.main([str(project), "--force", "--yes"])
    # Then no flow-proven root create remains, while source is still parseable.
    assert status == 0
    sources = {
        ".".join(path.relative_to(project).with_suffix("").parts): path.read_text(
            encoding="utf-8"
        )
        for path in project.rglob("*.py")
    }
    assert sources, "The corpus must exercise actual Python source"
    mapping = load_mapping()
    index = build_index(sources, mapping)
    for module, source in sources.items():
        tree = ast.parse(source)
        flow = Flow(Resolver(mapping, index), SourceContext(module, module, index))
        flow.analyze(source)
        for call in flow.calls:
            if call.kind == "client" and call.method == "create":
                assert isinstance(call.node.func, ast.Attribute)
                receiver = call.node.func.value
                assert isinstance(receiver, ast.Attribute) and receiver.attr == "v0", (
                    module,
                    ast.get_source_segment(source, call.node),
                )
        assert tree.body
