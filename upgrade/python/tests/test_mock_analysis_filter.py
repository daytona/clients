# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Known unrelated mock targets do not require the full SDK proof engine."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from daytona_upgrade import project_index
from daytona_upgrade.analysis_filter import AnalysisPlan
from daytona_upgrade.flow import Flow, build_index
from daytona_upgrade.models import Mapping, TransformOptions, load_mapping
from daytona_upgrade.project import preflight
from daytona_upgrade.project_index import ProjectLayout


@pytest.mark.parametrize(
    "source",
    [
        "from unittest.mock import patch\nwith patch('sys.stdout'): pass\n",
        "from unittest.mock import patch as p\nwith p.object(service, 'run'): pass\n",
        "import unittest.mock as mocks\nwith mocks.patch.multiple(service, run=replacement): pass\n",
        "import unittest\nclass Tests(unittest.TestCase):\n def test_run(self):\n  with unittest.mock.patch('sys.stdout'): pass\n",
    ],
)
def test_skips_unlinked_mock_module_when_targets_are_known_unrelated(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    # Given unrelated mock-heavy code next to a real Daytona module.
    analyze = Flow.analyze

    def track(self: Flow, text: str, module: ast.Module | None = None) -> None:
        assert text != source, "unrelated literal patch entered flow analysis"
        analyze(self, text, module)

    monkeypatch.setattr(Flow, "analyze", track)
    mapping = load_mapping()
    # When choosing modules for the real project index.
    index = build_index(
        {"app": "from daytona import Daytona\n", "test_catalog": source}, mapping
    )
    # Then ownership remains even though receiver analysis is omitted.
    assert index.owns("test_catalog")
    assert ("test_catalog", source) in index.skipped_sources


@pytest.mark.parametrize(
    "source",
    [
        "from unittest.mock import patch\npatch(target)\n",
        "from unittest.mock import patch as p\np(target=target)\n",
        "import unittest as u\nu.mock.patch(target)\n",
        "from unittest.mock import patch\nalias = patch\nalias(target)\n",
        "import unittest.mock as mocks\nalias = mocks.patch.object\nalias(target, 'create')\n",
        "from unittest.mock import patch\npatch(*arguments)\n",
        "from unittest.mock import patch\npatch(**arguments)\n",
        "patch(target)\n",
        "mocker.patch(target)\n",
    ],
)
def test_retains_analysis_when_a_mock_target_is_unresolved(source: str) -> None:
    # Given mock helpers that can emit review items independently of SDK imports.
    # When choosing modules for analysis.
    index = build_index({"test_app": source}, load_mapping())
    # Then dynamic targets and escaped helpers retain their existing diagnostics.
    assert ("test_app", source) not in index.skipped_sources


@pytest.mark.parametrize(
    "source",
    [
        "from unittest.mock import patch\nwith patch('sys.stdout'): pass\n",
        "from unittest.mock import patch\nwith patch.object(unknown, 'create'): pass\n",
        "import unittest\nclass Tests(unittest.TestCase):\n def test_run(self):\n  with unittest.mock.patch('sys.stdout'): pass\n",
        "from unittest.mock import patch\npatch(target)\n",
        "from unittest.mock import patch\nfrom app import client\nwith patch.object(client, 'create'): pass\n",
        "import mock as doubles\nwith doubles.patch('sys.stdout'): pass\n",
    ],
)
def test_keeps_results_when_literal_mock_filtering_replaces_full_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    # Given both linked and unrelated mocks alongside the same SDK application.
    (tmp_path / "app.py").write_text(
        "from daytona import Daytona\nclient = Daytona()\nclient.create()\n"
    )
    (tmp_path / "test_app.py").write_text(source)
    options = TransformOptions(load_mapping())
    plan_analysis = project_index.plan_analysis

    def full_analysis(
        sources: dict[str, str], mapping: Mapping, layout: ProjectLayout
    ) -> AnalysisPlan:
        return replace(
            plan_analysis(sources, mapping, layout), modules=frozenset(sources)
        )

    with monkeypatch.context() as patch:
        patch.setattr(project_index, "plan_analysis", full_analysis)
        reference = preflight(tmp_path, options)
    # When filtering the same sources normally.
    result = preflight(tmp_path, options)
    # Then edits and all located review items match the full-flow result.
    assert result == reference
