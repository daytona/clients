# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Negative controls for annotation proofs and scan boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.flow import SourceContext
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.resolution import ProjectIndex
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    "source",
    [
        'import daytona\nvalue = "daytona.Sandbox"\n',
        'import daytona\nfrom typing import Literal\ndef f(x: Literal["daytona.Sandbox"]): pass\n',
        'import daytona\nfrom typing import Literal as L\ndef f(x: L["daytona.Sandbox"]): pass\n',
        'import daytona\nfrom typing import Annotated\ndef f(x: Annotated[int, "daytona.Sandbox"]): pass\n',
        'import daytona\ndef f(daytona, x: "daytona.v0.Sandbox"): pass\n',
        "class Daytona: pass\nc: Daytona = unknown\nc.create()\n",
        "from daytona import Daytona\nclass Other: pass\nc: Daytona = Other()\nc.create()\n",
    ],
)
def test_preserves_source_when_string_or_client_is_not_a_moved_sdk_reference(
    source: str,
) -> None:
    # Given unrelated literal data, metadata, explicit namespaces or local types.
    options = TransformOptions(load_mapping())
    # When applying binding-aware migration.
    result = transform_source(
        source, options, SourceContext("app.py", "app", ProjectIndex({}))
    )
    # Then neither source nor diagnostics invent SDK evidence.
    assert result.source == source
    assert result.items == ()


def test_preserves_bare_forward_reference_when_its_import_moves() -> None:
    # Given a bare imported type used inside a string annotation.
    source = 'from daytona import Sandbox\ndef f(x: "Sandbox"): pass\n'
    # When migrating the binding's owning import.
    result = transform_source(
        source,
        TransformOptions(load_mapping()),
        SourceContext("app.py", "app", ProjectIndex({})),
    )
    # Then the imported name still resolves without rewriting the bare reference.
    assert result.source == source.replace(
        "from daytona import", "from daytona.v0 import"
    )
    assert result.items == ()


@pytest.mark.parametrize("declaration", ["", "from mycorp import Daytona\n"])
def test_reports_client_when_annotation_has_no_proven_sdk_binding(
    declaration: str,
) -> None:
    # Given an SDK-like annotation with missing or external provenance.
    source = declaration + "c: Daytona = unknown\nc.create()\n"
    # When the tool cannot resolve that annotation to the SDK.
    result = transform_source(
        source,
        TransformOptions(load_mapping()),
        SourceContext("app.py", "app", ProjectIndex({})),
    )
    # Then the annotation justifies a FIXME, not an automatic namespace rewrite.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert "c.v0.create()" not in result.source


@pytest.mark.parametrize("nested", [False, True])
def test_reports_only_partial_scan_when_shared_package_is_outside_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], nested: bool
) -> None:
    # Given a checkout with a shared client and a consumer, but no parent manifest.
    (tmp_path / ".git").mkdir()
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "__init__.py").write_text("from daytona import Daytona\nc = Daytona()\n")
    service = tmp_path / "service"
    service.mkdir()
    (service / "app.py").write_text("from shared import c\nc.create()\n")
    (service / "requirements.txt").write_text("daytona<1\n")
    # When inspecting the entire checkout or only one service, including strict mode.
    status = main([str(service if nested else tmp_path), "--dry", "--json", "--strict"])
    # Then only a partial scan emits one non-blocking completeness notice.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert [item["kind"] for item in report["items"]] == (
        ["types-unresolved"] if nested else []
    )
