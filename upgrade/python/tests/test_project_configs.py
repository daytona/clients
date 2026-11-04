# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Shared config mutation must fence the declaring module's client calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daytona_upgrade import cli


@pytest.mark.parametrize(
    "importer",
    [
        "from app import config, main\nconfig.api_url = 'https://self.example/api'\nmain()\n",
        "import os, app\nif os.environ.get('SELF_HOSTED'):\n    setattr(app.config, 'api_url', os.environ['URL'])\n",
        "from app import *\nconfig.api_url = 'https://self.example/api'\n",
        "from app import config as cfg\ncustomize(cfg)\n",
        "import app as application\ncustomize(application.config)\n",
        "from app import config\nalias = config\n",
        "from bridge import config\ncustomize(config)\n",
        "from app import config\nconfig.api_key = 'replacement'\n",
        "import app\nsetattr(app.config, 'api_url', None)\n",
        "import app\ncustomize(app.config)\nimport unrelated as app\n",
    ],
)
@pytest.mark.parametrize("drop", [False, True])
def test_shared_config_is_unverified_when_importer_can_mutate(
    tmp_path: Path, importer: str, drop: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a module config consumed inside a function and an external mutation.
    source = (
        "from daytona import Daytona, DaytonaConfig\n"
        "config = DaytonaConfig(api_key='k')\n"
        "def main():\n    Daytona(config).create()\n"
        "class Service:\n    def run(self):\n        Daytona(config).create()\n"
    )
    files = {
        "app.py": source,
        "settings.py": importer,
        "bridge.py": "from app import config\n",
        "requirements.txt": "daytona==0.224.0\n",
    }
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    # When preflighting through the real CLI, optionally asserting Cloud usage.
    status = cli.main(
        [str(tmp_path), "--force", "--yes", "--json"]
        + (["--drop-api-url"] if drop else [])
    )
    report = json.loads(capsys.readouterr().out)
    # Then safety is reported on the declaring module, atomically without assertion.
    assert status == (0 if drop else 2)
    assert any(
        item["file"] == "app.py"
        and item["kind"] == ("config-field" if drop else "custom-api-url")
        for item in report["items"]
    )
    if not drop:
        assert {name: (tmp_path / name).read_text() for name in files} == files


@pytest.mark.parametrize(
    "reader",
    [
        "from app import config\nprint(config)\n",
        "import app\nprint(app.config)\n",
        "from app import *\nprint(config)\n",
        "from bridge import config\nprint(config)\n",
        "import app\nconsume(app.config.api_key)\n",
        "from app import config\ndef unrelated(config):\n    customize(config)\n",
    ],
)
def test_shared_config_resolves_when_importer_is_readonly(
    tmp_path: Path, reader: str
) -> None:
    # Given harmless readers and a same-spelled unrelated function parameter.
    source = (
        "from daytona import Daytona, DaytonaConfig\nconfig = DaytonaConfig()\n"
        "def main():\n    Daytona(config).create()\n"
    )
    (tmp_path / "app.py").write_text(source)
    (tmp_path / "bridge.py").write_text("from app import config\n")
    (tmp_path / "run.py").write_text(reader)
    # When upgrading the project.
    status = cli.main([str(tmp_path), "--force", "--yes"])
    # Then external observation does not defeat the local origin proof.
    assert status == 0
    assert (tmp_path / "app.py").read_text() == source.replace(
        ".create()", ".v0.create()"
    )


@pytest.mark.parametrize(
    "importer",
    [
        "import pkg.app\ncustomize(pkg.app.config)\n",
        "from pkg import app\ncustomize(app.config)\n",
        "from .app import config\ncustomize(config)\n",
        "from . import app\ncustomize(app.config)\n",
        "from .bridge import *\ncustomize(config)\n",
    ],
)
@pytest.mark.parametrize("package_init", [False, True])
def test_shared_config_is_unverified_when_import_is_package_qualified(
    tmp_path: Path, importer: str, package_init: bool
) -> None:
    # Given relative imports, re-exports, or a namespace package's module import.
    package = tmp_path / "pkg"
    package.mkdir()
    source = (
        "from daytona import Daytona, DaytonaConfig\nconfig = DaytonaConfig()\n"
        "def main():\n    Daytona(config).create()\n"
    )
    (package / "app.py").write_text(source)
    (package / "bridge.py").write_text("from .app import config\n")
    (package / "settings.py").write_text(importer)
    if package_init:
        (package / "__init__.py").write_text("")
    # When inspecting the full project.
    status = cli.main([str(tmp_path), "--force", "--yes"])
    # Then module spelling cannot hide the escaping config's true owner.
    assert status == 2
    assert (package / "app.py").read_text() == source
