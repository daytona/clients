# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The INSTALLED package type-checks for users: it ships `py.typed`, the root
error classes type the fields both namespaces set, and removed root names are
errors. mypy runs from a scratch directory against this interpreter's
site-packages (no MYPYPATH, no `src` on the path), as a user's mypy would."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
mypy_api = pytest.importorskip("mypy.api")


def _mypy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str) -> tuple[str, int]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MYPYPATH", raising=False)
    snippet = tmp_path / "user_code.py"
    snippet.write_text(source)
    stdout, stderr, status = mypy_api.run(
        [
            "--strict",
            "--no-incremental",
            "--python-executable",
            sys.executable,
            "--python-version",
            "3.10",
            "--config-file",
            os.devnull,
            str(snippet),
        ]
    )
    return stdout + stderr, status


def test_the_installed_package_ships_py_typed() -> None:
    import daytona

    assert (Path(daytona.__file__).parent / "py.typed").is_file()


def test_the_readme_error_example_type_checks_against_the_installed_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    readme = (ROOT / "README.md").read_text()
    block = next(
        b
        for b in re.findall(r"```python\n(.*?)```", readme, re.DOTALL)
        if "except daytona.DaytonaNotFoundError" in b
    )
    source = (
        "from daytona import Daytona\n\nd = Daytona()\n"
        + block
        + "\n\ndef handle(e: daytona.DaytonaError) -> str:\n"
        "    status: int | None = e.status_code\n"
        "    code: str | None = e.code\n"
        "    source: str | None = e.source\n"
        "    return f'{e.message} {status} {code} {source}'\n"
    )
    output, status = _mypy(tmp_path, monkeypatch, source)
    assert status == 0, output


def test_removed_root_names_are_type_errors_for_users(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, status = _mypy(tmp_path, monkeypatch, "from daytona import Sandbox\n\nprint(Sandbox)\n")
    assert status == 1, output
    assert 'Module "daytona" has no attribute "Sandbox"' in output


def test_v2_imports_type_check_under_strict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The README's `from daytona.v2 import …` path passes mypy --strict
    (no_implicit_reexport needs a statically readable __all__)."""
    output, status = _mypy(
        tmp_path,
        monkeypatch,
        "from daytona.v2 import AsyncSandbox, ImageBuilder, NotFoundError, Sandbox\n"
        "def use(s: Sandbox, a: AsyncSandbox, b: ImageBuilder, e: NotFoundError) -> None: ...\n",
    )
    assert status == 0, output


def test_volume_surfaces_type_check_for_users(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output, status = _mypy(
        tmp_path,
        monkeypatch,
        "from daytona import Daytona\n"
        "from daytona.v2 import VolumeMount, VolumesService, SandboxVolumesService\n"
        "d = Daytona()\n"
        "service: VolumesService = d.v2.volumes\n"
        "d.v2.create('python:3.12', volumes=[VolumeMount(volume_id='vol', mount_path='/mnt/data', subpath='proj')])\n"
        "pull: SandboxVolumesService = d.v2.get('sbx').volumes\n",
    )
    assert status == 0, output
