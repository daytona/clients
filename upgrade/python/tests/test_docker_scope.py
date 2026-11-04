# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Docker instruction boundaries and sibling dependency ownership regressions."""

from __future__ import annotations

from pathlib import Path

import pytest

from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.project import preflight


@pytest.mark.parametrize(
    "declaration",
    [
        "ENV DAYTONA_API_URL=https://self/api",
        "ENV DAYTONA_SERVER_URL https://self/api",
        "ARG DAYTONA_API_URL=https://self/api",
    ],
)
def test_docker_refuses_when_onbuild_declares_custom_endpoint(
    tmp_path: Path, declaration: str
) -> None:
    # Given a deferred environment declaration with a custom endpoint.
    (tmp_path / "Dockerfile").write_text("ONBUILD " + declaration + "\n")
    # When scanning the Docker instructions.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then ONBUILD has exactly the environment declaration's refusal semantics.
    assert [item["kind"] for item in result.items] == ["env-api-url", "custom-api-url"]


@pytest.mark.parametrize("marker", ["<<EOF", "<< EOF", "<<-EOF", "<<- EOF"])
@pytest.mark.parametrize("quote", ["", "'", '"'])
def test_docker_skips_payload_when_heredoc_delimiter_is_spaced_or_quoted(
    tmp_path: Path, marker: str, quote: str
) -> None:
    # Given instruction-shaped payload text and a real declaration after its end.
    operator = marker.removesuffix("EOF")
    tab = "\t" if "-" in operator else ""
    source = (
        f"RUN {operator}{quote}EOF{quote}\n"
        "ENV DAYTONA_API_URL=https://ignored/api\n"
        f"{tab}EOF\n"
        "ENV DAYTONA_SERVER_URL=https://self/api\n"
    )
    (tmp_path / "Dockerfile").write_text(source)
    # When scanning actual Docker declarations.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then only the real post-heredoc declaration is reported.
    assert [(item["kind"], item["line"]) for item in result.items] == [
        ("env-api-url", 4),
        ("custom-api-url", 4),
    ]


@pytest.mark.parametrize(
    "instruction",
    [
        'ONBUILD RUN echo "DAYTONA_API_URL=https://self/api"',
        'ONBUILD LABEL example="DAYTONA_API_URL=https://self/api"',
        'ONBUILD ENV OTHER="DAYTONA_API_URL=https://self/api"',
        "ONBUILD ONBUILD ENV DAYTONA_API_URL=https://self/api",
    ],
)
def test_docker_ignores_names_when_onbuild_is_not_an_endpoint_declaration(
    tmp_path: Path, instruction: str
) -> None:
    # Given a non-environment trigger or a variable name inside an unrelated value.
    (tmp_path / "Dockerfile").write_text(instruction + "\n")
    # When scanning declaration keys only.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then an ONBUILD prefix does not broaden scanning to arbitrary text.
    assert result.items == ()


@pytest.mark.parametrize("consumer", ["app.py", "src/app.py"])
def test_dependency_stays_manual_when_only_sibling_declares_sdk(
    tmp_path: Path, consumer: str
) -> None:
    # Given two independent package roots, only one declaring the legacy SDK.
    for name, dependencies in [("a", '["daytona==0.224.0"]'), ("b", "[]")]:
        project = tmp_path / name
        project.mkdir()
        (project / "pyproject.toml").write_text(
            f'[project]\nname="{name}"\ndependencies={dependencies}\n'
        )
        source = project / consumer
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("from daytona import Daytona\nDaytona().create()\n")
    # When migrating both projects in a single invocation.
    result = preflight(tmp_path, TransformOptions(load_mapping()))
    # Then the sibling without an installing declaration gets its own warning.
    assert [
        item["file"] for item in result.items if item["kind"] == "manual-dependency"
    ] == [f"b/{consumer}"]
