# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Exercise field refusals through the actual CLI and migrated program."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("bound", [False, True])
def test_mock_assertion_survives_when_cli_cannot_prove_constructor_argument(
    tmp_path: Path, bound: bool
) -> None:
    # Given a runnable program overriding a concrete SDK default with a mock.
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")
    app = project / "app.py"
    access = "w.client.create()" if bound else "Worker(client=fake).client.create()"
    source = (
        "from dataclasses import dataclass\nfrom unittest.mock import MagicMock\n"
        "from daytona import Daytona\n@dataclass\nclass Worker:\n    client: Daytona = Daytona()\n"
        "fake = MagicMock()\n"
        + ("w = Worker(client=fake)\n" if bound else "")
        + access
        + "\nfake.create.assert_called_once()\n"
    )
    app.write_text(source)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "daytona.py").write_text("class Daytona: pass\n")
    # When invoking strict migration, then running the resulting user program.
    migrated = subprocess.run(
        [
            sys.executable,
            "-m",
            "daytona_upgrade",
            str(project),
            "--json",
            "--strict",
            "--force",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    executed = subprocess.run(
        [sys.executable, str(app)],
        env={**os.environ, "PYTHONPATH": str(runtime)},
        text=True,
        capture_output=True,
        check=False,
    )
    # Then strict reports review while preserving the mock's original call contract.
    assert migrated.returncode == 1, migrated.stderr
    assert [item["kind"] for item in json.loads(migrated.stdout)["items"]] == ["fixme"]
    assert executed.returncode == 0, executed.stderr
