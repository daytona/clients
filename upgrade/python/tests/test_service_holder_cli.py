# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Drive service-field migration through strict CLI execution."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "operation", ["params = self._params()", "helper(self)", "str(self)"]
)
@pytest.mark.parametrize("annotated", [False, True])
def test_strict_service_migration_when_holder_proof_is_retained_or_fenced(
    tmp_path: Path, operation: str, annotated: bool
) -> None:
    # Given a service and an external runtime SDK that distinguishes v0 dispatch.
    project = tmp_path / "project"
    project.mkdir()
    (project / "requirements.txt").write_text("daytona[v0-sdk]>=2,<3\n")
    app = project / "app.py"
    app.write_text(
        "from daytona import Daytona\ndef helper(value): pass\nclass Service:\n"
        "    def __init__(self):\n        self.daytona"
        + (": Daytona" if annotated else "")
        + " = Daytona()\n    def _params(self): return {}\n"
        f"    def run(self):\n        {operation}\n        return self.daytona.create()\n"
        "print(Service().run())\n"
    )
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    fenced = not annotated and operation != "params = self._params()"
    (runtime / "daytona.py").write_text(
        "class V0:\n    def create(self): return 'sandbox'\n"
        "class Daytona:\n    def __init__(self): self.v0 = V0()\n"
        "    def create(self): return 'manual'\n"
    )
    # When invoking strict migration through the real CLI.
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
    # Then retained proofs execute v0; the external untyped escape stays manual.
    assert migrated.returncode == (1 if fenced else 0), migrated.stderr
    assert [item["kind"] for item in json.loads(migrated.stdout)["items"]] == (
        ["fixme"] if fenced else []
    )
    assert executed.returncode == 0, executed.stderr
    assert executed.stdout.strip() == ("manual" if fenced else "sandbox")
