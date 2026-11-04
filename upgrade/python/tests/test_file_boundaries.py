# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""File descriptor identity and checkout boundaries govern deployment inputs."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.regular_files import INPUT_LIMIT


@pytest.fixture(autouse=True)
def checkout_environment_root(monkeypatch: pytest.MonkeyPatch) -> None:
    # Supply the checkout boundary independently of the host's Git layout.
    monkeypatch.setattr(
        "daytona_upgrade.project_discovery.environment_base", lambda base: base
    )
    monkeypatch.setattr("daytona_upgrade.project.environment_base", lambda base: base)


@pytest.mark.skipif(sys.platform == "win32", reason="Requires POSIX FIFOs")
def test_rejects_fifo_when_regular_path_is_swapped_at_open(tmp_path: Path) -> None:
    # Given a regular input replaced immediately before either opening API.
    path = tmp_path / "input.conf"
    path.write_bytes(b"regular")
    driver = textwrap.dedent("""
        import errno
        import os
        import sys
        from pathlib import Path
        from unittest.mock import patch
        from daytona_upgrade.regular_files import read_regular_bytes

        path = Path(sys.argv[1])
        original_os_open = os.open
        original_path_open = Path.open

        def swap():
            path.unlink()
            os.mkfifo(path)

        def swapped_os_open(file, flags):
            swap()
            return original_os_open(file, flags)

        def swapped_path_open(file, mode):
            swap()
            return original_path_open(file, mode)

        with patch('os.open', swapped_os_open), patch.object(Path, 'open', swapped_path_open):
            try:
                read_regular_bytes(path)
            except OSError as error:
                assert error.errno == errno.EINVAL, error
            else:
                raise AssertionError('FIFO accepted as a regular file')
    """)
    # When the real reader opens the swapped input with no FIFO writer.
    result = subprocess.run(
        [sys.executable, "-c", driver, str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    # Then descriptor validation rejects it without blocking.
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("deployment", [False, True])
def test_reviews_large_yaml_only_when_deployment_directory_is_inside_checkout(
    tmp_path: Path, deployment: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given an external deploy ancestor and a controlled checkout boundary.
    root = tmp_path / "deploy" / "checkout"
    root.mkdir(parents=True)
    directory = root / "deploy" if deployment else root
    directory.mkdir(exist_ok=True)
    (directory / "schema.yml").write_text("#" + "x" * INPUT_LIMIT + "\n")
    (root / "requirements.txt").write_text("daytona==0.224.0\n")
    # When discovery applies its size budget using checkout-relative provenance.
    status = main([str(root), "--force", "--dry", "--strict", "--json"])
    # Then only a deployment directory inside the checkout requires review.
    report = json.loads(capsys.readouterr().out)
    assert status == (1 if deployment else 0)
    assert [item["kind"] for item in report["items"]] == (
        ["env-api-url"] if deployment else []
    )
