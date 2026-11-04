# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Cheap filtering retains import, fixture, environment and syntax boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from daytona_upgrade.flow import Flow, SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight
from daytona_upgrade.transform import transform_source


def test_unrelated_files_skip_flow_when_project_contains_sdk_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given unrelated source beside a real SDK module.
    irrelevant = "def calculate(value):\n return value + 1\n"
    sources = {
        "app": "from daytona import Daytona\nd = Daytona()\n",
        "maths": irrelevant,
    }
    analyze = Flow.analyze

    def track(self: Flow, source: str, module: ast.Module | None = None) -> None:
        assert source != irrelevant, "unrelated source entered the flow analyzer"
        analyze(self, source, module)

    monkeypatch.setattr(Flow, "analyze", track)
    mapping = load_mapping()
    # When indexing and transforming the unrelated file.
    index = build_index(sources, mapping)
    result = transform_source(
        irrelevant, TransformOptions(mapping), SourceContext("maths.py", "maths", index)
    )
    # Then it is unchanged and remains first-party for import ownership.
    assert result.source == irrelevant
    assert index.owns("maths")


@pytest.mark.parametrize(
    "sources, consumer",
    [
        (
            {
                "owner": "from daytona import Daytona\nclient = Daytona()\n",
                "app": "from owner import client\nclient.create()\n",
            },
            "app",
        ),
        (
            {
                "owner": "from daytona import Daytona\nclient = Daytona()\n",
                "bridge": "from owner import client\n",
                "app": "import bridge\nbridge.client.create()\n",
            },
            "app",
        ),
        (
            {
                "pkg.owner": "from daytona import Daytona\nclient = Daytona()\n",
                "pkg.app": "from .owner import client\nclient.create()\n",
            },
            "pkg.app",
        ),
        (
            {
                "pkg": "from .owner import client\n",
                "pkg.owner": "from daytona import Daytona\nclient = Daytona()\n",
                "app": "from pkg import client\nclient.create()\n",
            },
            "app",
        ),
    ],
)
def test_consumers_are_kept_when_sdk_identity_crosses_project_imports(
    sources: dict[str, str], consumer: str
) -> None:
    # Given a client carried through ordinary, relative or package imports.
    mapping = load_mapping()
    files = {name: name.replace(".", "/") + ".py" for name in sources}
    if "pkg" in sources:
        files["pkg"] = "pkg/__init__.py"
    # When the consumer is migrated using the filtered project index.
    index = build_index(sources, mapping, files)
    result = transform_source(
        sources[consumer],
        TransformOptions(mapping),
        SourceContext(files[consumer], consumer, index),
    )
    # Then imported receiver identity still certifies its v0 call.
    assert ".v0.create()" in result.source


@pytest.mark.parametrize(
    "source",
    [
        "import importlib\nm = importlib.import_module('day\\x74ona.common.sandbox')\n",
        "m = __import__('day\\x74ona.common.sandbox')\n",
    ],
)
def test_dynamic_imports_are_kept_when_sdk_module_name_is_escaped(source: str) -> None:
    # Given a decoded SDK target absent from the raw source spelling.
    mapping = load_mapping()
    # When the normal transform runs with the project index.
    index = build_index({"app": source}, mapping)
    result = transform_source(
        source, TransformOptions(mapping), SourceContext("app.py", "app", index)
    )
    # Then the namespace rewrite and dynamic-import diagnostic remain observable.
    assert "daytona.v0.common.sandbox" in result.source
    assert any(item["kind"] == "dynamic-import" for item in result.items)


def test_syntax_errors_still_block_when_file_has_no_sdk_references(
    tmp_path: Path,
) -> None:
    # Given a malformed unrelated file beside SDK code.
    (tmp_path / "app.py").write_text(
        "from daytona import Daytona\nd = Daytona()\nd.create()\n"
    )
    (tmp_path / "broken.py").write_text("value = (\n")
    # When the whole project is inspected.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then syntax failure still prevents every write.
    assert not result.changes
    assert any(
        item["file"] == "broken.py" and item["kind"] == "unsupported-file"
        for item in result.items
    )


def test_environment_alias_consumers_remain_scanned_when_sdk_is_elsewhere(
    tmp_path: Path,
) -> None:
    # Given an environment writer using a first-party module alias.
    (tmp_path / "app.py").write_text("from daytona import Daytona\nd = Daytona()\n")
    (tmp_path / "system.py").write_text("import os\n")
    (tmp_path / "writer.py").write_text(
        "from system import os as runtime\nruntime.environ['DAYTONA_API_URL'] = 'https://private.example/api'\n"
    )
    # When project filtering and environment inspection run.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then a custom environment URL still refuses migration.
    assert any(
        item["kind"] == "custom-api-url" and item["file"] == "writer.py"
        for item in result.items
    )


def test_provider_exports_are_kept_when_sdk_module_imports_non_sdk_helpers() -> None:
    # Given a non-SDK helper that definitely returns an unrelated object.
    sources = {
        "helpers": "class Other:\n def create(self): pass\ndef make() -> Other:\n return Other()\n",
        "app": "from daytona import Daytona\nfrom helpers import make\nd = make()\nd.create()\n",
    }
    mapping = load_mapping()
    # When the consumer's calls are inspected.
    index = build_index(sources, mapping)
    result = transform_source(
        sources["app"], TransformOptions(mapping), SourceContext("app.py", "app", index)
    )
    # Then the known unrelated return is preserved, not treated as a client.
    assert result.source == sources["app"]
    assert not result.items


def test_implicit_fixtures_are_kept_when_test_module_has_no_sdk_import(
    tmp_path: Path,
) -> None:
    # Given a client supplied through pytest's implicit conftest relationship.
    (tmp_path / "conftest.py").write_text(
        "import pytest\nfrom daytona import Daytona\n@pytest.fixture\ndef client() -> Daytona:\n return Daytona()\n"
    )
    (tmp_path / "test_app.py").write_text("def test_run(client):\n client.create()\n")
    # When project indexing and transformation run.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then its implicit receiver still receives review-only fixture evidence.
    assert any(
        item["file"] == "test_app.py" and item["kind"] == "fixme"
        for item in result.items
    )


def test_different_file_contents_still_analyze_when_module_index_used_its_stub() -> (
    None
):
    # Given a .pyi source that replaced its .py module key during discovery.
    mapping = load_mapping()
    index = build_index({"app": "value: int\n"}, mapping)
    source = "from daytona import Daytona\nd = Daytona()\nd.create()\n"
    # When the actual .py file is transformed using that shared module index.
    result = transform_source(
        source, TransformOptions(mapping), SourceContext("app.py", "app", index)
    )
    # Then filtering only skips the exact source that was inspected, not its sibling.
    assert "d.v0.create()" in result.source


def test_os_path_utility_skips_flow_when_it_has_no_sdk_or_environment_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an ordinary filesystem utility with no SDK or environment access.
    source = "import os\nroot = os.path.dirname(__file__)\n"

    def reject_analysis(
        self: Flow, source: str, module: ast.Module | None = None
    ) -> None:
        pytest.fail("unrelated filesystem utility entered the flow analyzer")

    monkeypatch.setattr(Flow, "analyze", reject_analysis)
    mapping = load_mapping()
    # When indexing and transforming that utility.
    index = build_index({"utility": source}, mapping)
    result = transform_source(
        source, TransformOptions(mapping), SourceContext("utility.py", "utility", index)
    )
    # Then importing os alone does not force receiver analysis.
    assert result.source == source
    assert result.items == ()
