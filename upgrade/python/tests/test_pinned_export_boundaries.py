# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Older root exports absent from the pinned bundle require manual review."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import daytona_upgrade
from daytona_upgrade.flow import SourceContext
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight
from daytona_upgrade.resolution import ProjectIndex
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    "source",
    [
        "from daytona import AsyncPaginatedSandboxes as Pages\n",
        "from daytona_sdk import Daytona, AsyncPaginatedSandboxes\n",
        "from daytona import Daytona; from daytona import AsyncPaginatedSandboxes\n",
        "import daytona as sdk\npages = sdk.AsyncPaginatedSandboxes\n",
    ],
)
def test_reports_private_import_when_missing_from_pinned_exports(
    tmp_path: Path, source: str
) -> None:
    # Given a shared mapping with the pinned release's exports, not the historical name.
    raw = json.loads(
        Path(daytona_upgrade.__file__).with_name("mapping.json").read_text()
    )
    raw["v0Exports"] = {"py": ["Sandbox", "Image"]}
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps(raw))
    # When migrating a historical root import.
    result = transform_source(
        source,
        TransformOptions(load_mapping(path)),
        SourceContext("app.py", "app", ProjectIndex({})),
    )
    # Then the import is migrated but the unsupported symbol is not silently approved.
    assert "v0" in result.source
    assert [item["kind"] for item in result.items] == ["private-import"]
    assert "AsyncPaginatedSandboxes" in result.items[0]["message"]


def test_skips_export_check_when_shared_mapping_omits_export_list(
    tmp_path: Path,
) -> None:
    # Given an older shared mapping without the optional export list.
    raw = json.loads(
        Path(daytona_upgrade.__file__).with_name("mapping.json").read_text()
    )
    raw.pop("v0Exports", None)
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps(raw))
    # When migrating a resource import with no authoritative export list.
    result = transform_source(
        "from daytona import AsyncPaginatedSandboxes\n",
        TransformOptions(load_mapping(path)),
        SourceContext("app.py", "app", ProjectIndex({})),
    )
    # Then no speculative export diagnostic is produced.
    assert result.items == ()


@pytest.mark.parametrize(
    "source",
    [
        "from daytona import Sandbox, Image, Daytona\n",
        "import daytona as sdk\nsb = sdk.Sandbox\n",
        "from .daytona import AsyncPaginatedSandboxes\n",
        "import daytona\ndef f(daytona):\n    return daytona.AsyncPaginatedSandboxes\n",
    ],
)
def test_preserves_import_diagnostics_when_known_export_or_local_binding(
    tmp_path: Path, source: str
) -> None:
    # Given an authoritative export list and a valid or unrelated import.
    raw = json.loads(
        Path(daytona_upgrade.__file__).with_name("mapping.json").read_text()
    )
    raw["v0Exports"] = {"py": ["Sandbox", "Image"]}
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps(raw))
    # When applying the normal upgrade.
    result = transform_source(
        source,
        TransformOptions(load_mapping(path)),
        SourceContext("app.py", "app", ProjectIndex({})),
    )
    # Then exported symbols and unrelated bindings are not flagged as private.
    assert result.items == ()


def test_reports_only_private_import_when_project_dependency_is_handled(
    tmp_path: Path,
) -> None:
    # Given a project with a migratable dependency and an obsolete root export.
    (tmp_path / "requirements.txt").write_text("daytona==0.120.0\n")
    (tmp_path / "app.py").write_text("from daytona import AsyncPaginatedSandboxes\n")
    raw = json.loads(
        Path(daytona_upgrade.__file__).with_name("mapping.json").read_text()
    )
    raw["v0Exports"] = {"py": ["Sandbox"]}
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps(raw))
    # When project preflight migrates both the dependency and the import.
    result = preflight(tmp_path, TransformOptions(load_mapping(path)))
    # Then the export problem is not misreported as an unhandled dependency.
    assert [item["kind"] for item in result.items] == ["private-import"]
    assert len(result.dependencies) == 1
