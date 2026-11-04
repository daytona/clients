# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Setuptools list semantics and expanded interpreter constraints stay visible."""

from __future__ import annotations

import json
from configparser import ConfigParser
from pathlib import Path

import pytest
from packaging.requirements import Requirement

from daytona_upgrade.cli import main
from daytona_upgrade.manifests import inspect_manifest
from daytona_upgrade.models import load_mapping
from daytona_upgrade.python_floor import PYTHON_REVIEW


@pytest.mark.parametrize(
    "value",
    [
        "sphinx; daytona==0.224.0",
        "daytona==0.224.0; sphinx",
        "\n    sphinx\n    daytona==0.224.0",
        '\n    sphinx\n    daytona==0.224.0; python_version >= "3.10"',
    ],
)
def test_migrates_each_requirement_when_setup_cfg_extra_is_a_list(value: str) -> None:
    # Given single-line list-semi or dangling multiline setuptools requirements.
    source = f"[options.extras_require]\ndocs = {value}\n"
    # When migrating without flattening list items into a PEP 508 marker.
    result = inspect_manifest(Path("setup.cfg"), source, load_mapping())
    # Then setuptools' item boundaries still produce compatible dependencies.
    parser = ConfigParser()
    parser.read_string(result.source)
    parsed = parser["options.extras_require"]["docs"]
    requirements = [
        Requirement(item.strip())
        for item in (parsed.splitlines() if "\n" in parsed else parsed.split(";"))
        if item.strip()
    ]
    daytona = next(item for item in requirements if item.name == "daytona")
    assert "2.0" in daytona.specifier
    assert "0.224.0" not in daytona.specifier
    assert daytona.extras == {"v0-sdk"}
    assert any(item.name == "sphinx" for item in requirements)
    assert result.manual == ()
    assert not result.v2_found


@pytest.mark.parametrize(
    "value",
    ['daytona==0.224.0; python_version >= "3.10"', "\n    sphinx; daytona==0.224.0"],
)
def test_preserves_extra_when_setuptools_requirement_boundaries_are_ambiguous(
    value: str,
) -> None:
    # Given a marker in a single-line list or multiple requirements on a dangling line.
    source = f"[options.extras_require]\ndocs = {value}\n"
    # When inspecting an invalid or ambiguous setuptools requirement list.
    result = inspect_manifest(Path("setup.cfg"), source, load_mapping())
    # Then it is preserved with explicit manual work rather than mis-rewritten.
    assert result.source == source
    assert result.manual
    assert not result.v2_found


@pytest.mark.parametrize(
    ("section", "option", "dependency_file"),
    [
        ("options", "install_requires", "deps.txt"),
        ("options.extras_require", "docs", "a.txt, b.txt"),
    ],
)
def test_cli_reports_setuptools_requirements_file_for_manual_migration(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    section: str,
    option: str,
    dependency_file: str,
) -> None:
    # Given a setuptools file: declaration and a Daytona pin in a referenced file.
    manifest = tmp_path / "setup.cfg"
    source = f"[{section}]\n{option} = file: {dependency_file}\n"
    manifest.write_text(source)
    (tmp_path / "deps.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "a.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "b.txt").write_text("daytona==0.224.0\n")
    # When running the writing CLI in strict mode.
    status = main([str(tmp_path), "--force", "--yes", "--json", "--strict"])
    report = json.loads(capsys.readouterr().out)
    # Then the declaration is preserved and strict mode reports manual migration.
    assert status == 1
    assert manifest.read_text() == source
    assert any(
        item["kind"] == "manual-dependency" and item["file"] == "setup.cfg"
        for item in report["items"]
    )


def test_cli_refuses_setuptools_requirements_file_without_daytona(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a referenced requirements file containing no Daytona dependency.
    (tmp_path / "setup.cfg").write_text(
        "[options]\ninstall_requires = file: deps.txt\n"
    )
    (tmp_path / "deps.txt").write_text("requests>=2\n")
    # When running the strict migration CLI.
    status = main([str(tmp_path), "--force", "--yes", "--json", "--strict"])
    report = json.loads(capsys.readouterr().out)
    # Then the file reference provides no Daytona evidence and produces no item.
    assert status == 2
    assert report["items"] == []


@pytest.mark.parametrize("constraint", [">=3.9", ">=3.10", "unknown"])
@pytest.mark.parametrize("binding", ["literal", "name", "alias", "annotated"])
def test_reviews_floor_when_setup_expands_static_keywords(
    constraint: str, binding: str
) -> None:
    # Given a Python floor in a literal dictionary or a stable module binding.
    literal = f'{{"python_requires": "{constraint}"}}'
    assignments = {
        "literal": "",
        "name": f"kwargs = {literal}\n",
        "alias": f"kwargs = {literal}\nalias = kwargs\n",
        "annotated": f"kwargs: dict = {literal}\n",
    }
    expansion = (
        literal if binding == "literal" else "alias" if binding == "alias" else "kwargs"
    )
    source = (
        "from setuptools import setup\n"
        + assignments[binding]
        + f'setup(install_requires=["daytona==0.224"], **{expansion})\n'
    )
    # When inspecting setup kwargs without executing project code.
    result = inspect_manifest(Path("setup.py"), source, load_mapping())
    # Then only a proven compatible floor avoids the existing compatibility task.
    assert [message for _, message in result.manual] == (
        [] if constraint == ">=3.10" else [PYTHON_REVIEW]
    )
    assert all(line > 0 for line, _ in result.manual)


@pytest.mark.parametrize(
    "body",
    [
        "kwargs = unknown()\n",
        'kwargs = {"python_requires": ">=3.10"}\nkwargs.update(unknown())\n',
        'kwargs = {"python_requires": ">=3.10"}\nalias = kwargs\nalias.update(unknown())\n',
        'kwargs = {"python_requires": ">=3.10", **unknown()}\n',
        'kwargs = {"python_requires": ">=3.10"}\nif flag: kwargs = unknown()\n',
        'kwargs = {"python_requires": ">=3.10"}\ndel kwargs["python_requires"]\n',
        'kwargs = {key: ">=3.10"}\n',
        'kwargs = {"python_requires": [">=3.10"]}\n',
        'kwargs = {"python_requires": ">=3.10"}\nchange(kwargs)\n',
    ],
)
def test_reports_floor_when_setup_keyword_expansion_is_uncertain(body: str) -> None:
    # Given a dictionary expansion whose effective interpreter floor is uncertain.
    source = "from setuptools import setup\n" + body + "setup(**kwargs)\n"
    # When scanning static setuptools arguments.
    result = inspect_manifest(Path("setup.py"), source, load_mapping())
    # Then uncertainty is an explicit floor task, never silent certification.
    assert [message for _, message in result.manual] == [PYTHON_REVIEW]


def test_reports_original_floor_when_dictionary_captures_rebound_variable() -> None:
    # Given a dictionary that snapshots an old range before the variable changes.
    source = (
        'from setuptools import setup\nfloor = ">=3.9"\n'
        'kwargs = {"python_requires": floor}\nfloor = ">=3.10"\nsetup(**kwargs)\n'
    )
    # When inspecting the call-site dictionary proof.
    result = inspect_manifest(Path("setup.py"), source, load_mapping())
    # Then the later rebinding cannot erase the captured incompatible range.
    assert [message for _, message in result.manual] == [PYTHON_REVIEW]


@pytest.mark.parametrize(
    "dictionary", ['{"name": unknown()}', '{**{"python_requires": ">=3.10"}}']
)
def test_keeps_floor_clean_when_static_dictionary_has_no_unsafe_range(
    dictionary: str,
) -> None:
    # Given statically known keyword names, without an unsafe interpreter floor.
    source = f"from setuptools import setup\nsetup(**{dictionary})\n"
    # When inspecting the expanded arguments.
    result = inspect_manifest(Path("setup.py"), source, load_mapping())
    # Then unrelated dynamic values do not manufacture a floor warning.
    assert result.manual == ()


def test_cli_reports_when_setup_keyword_floor_is_unresolved(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a real project with an old dependency and dynamic setup kwargs.
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    (tmp_path / "setup.py").write_text(
        "from setuptools import setup\nsetup(**get_kwargs())\n"
    )
    # When requesting the real strict writing migration.
    status = main([str(tmp_path), "--force", "--yes", "--json", "--strict"])
    # Then the unverifiable floor is visible as a located manual dependency task.
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert any(
        item["kind"] == "manual-dependency" and item["file"] == "setup.py"
        for item in report["items"]
    )
