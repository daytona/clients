# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Filtering is an optimization, never a change to migration diagnostics."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from daytona_upgrade import project_index
from daytona_upgrade.analysis_filter import AnalysisPlan
from daytona_upgrade.models import Mapping, TransformOptions, load_mapping
from daytona_upgrade.project import preflight
from daytona_upgrade.project_index import ProjectLayout


@pytest.mark.parametrize(
    "extras",
    [
        {"plain.py": "def unrelated(value):\n return value.create()\n"},
        {"plain.py": "value = (\n"},
        {"hooks.py": "import builtins as runtime\nruntime.print = mutate\n"},
        {
            "hooks.py": "from daytona import DaytonaConfig as C\nC.__getattribute__ = hook\n"
        },
        {
            "conftest.py": "import pytest\nfrom daytona import Daytona\n@pytest.fixture\ndef client():\n return Daytona()\n",
            "plugin.py": "import pytest\n@pytest.fixture\ndef dependent(client):\n client.create()\n",
        },
        {
            "system.py": "import os\n",
            "writer.py": "from system import os as runtime\nruntime.environ[unknown_key] = unknown_value\n",
        },
        {
            "loader.py": "from importlib import import_module as load\nload(dynamic_name)\n"
        },
        {
            "helpers.py": "class Other:\n def create(self): pass\n",
            "consumer.py": "from helpers import Other\nvalue = Other()\nvalue.create()\n",
        },
    ],
)
def test_filtered_result_is_identical_when_full_analysis_is_the_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extras: dict[str, str]
) -> None:
    # Given project-wide fences, implicit imports and unrelated parse failures.
    (tmp_path / "app.py").write_text(
        "from daytona import Daytona, DaytonaConfig\ncfg = DaytonaConfig()\nprint(cfg)\nDaytona(cfg).create()\n"
    )
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    for name, source in extras.items():
        (tmp_path / name).write_text(source)
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
    # When the same real preflight uses conservative filtering.
    result = preflight(tmp_path, options)
    # Then source bytes, dependencies and all diagnostic fields are identical.
    assert result == reference
