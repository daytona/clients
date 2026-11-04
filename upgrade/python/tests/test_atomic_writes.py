# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Failure injection and real filesystem limits protect migration originals."""

from __future__ import annotations

import errno
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from daytona_upgrade.cli import main
from daytona_upgrade.writes import write_outputs


@pytest.mark.parametrize("nth", [1, 2, 3])
def test_restores_all_files_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nth: int
) -> None:
    # Given three sources and a failure on the Nth replacement.
    original = b"from daytona import Daytona\r\nDaytona().create()\r\n"
    paths = [tmp_path / f"{name}.py" for name in ("a", "b", "c")]
    for path in paths:
        path.write_bytes(original)
        path.chmod(0o751)
    replace = os.replace
    calls = 0

    def fail_replace(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == nth:
            raise OSError(errno.ENOSPC, "injected replace failure", str(destination))
        replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_replace)
    # When the actual CLI applies its planned changes.
    status = main([str(tmp_path), "--force", "--yes"])
    # Then no file changed and all staged outputs were removed.
    assert status == 1
    assert [path.read_bytes() for path in paths] == [original] * 3
    assert [path.stat().st_mode & 0o777 for path in paths] == [0o751] * 3
    assert sorted(tmp_path.iterdir()) == paths


def test_preserves_all_files_when_temp_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given two sources and a partial write failure on the second staged output.
    original = b"from daytona import Daytona\nDaytona().create()\n"
    paths = [tmp_path / "a.py", tmp_path / "b.py"]
    for path in paths:
        path.write_bytes(original)
    named_temp = tempfile.NamedTemporaryFile
    calls = 0

    @contextmanager
    def fail_temp(
        *, dir: Path, delete: bool, prefix: str, suffix: str
    ) -> Iterator[tempfile._TemporaryFileWrapper[bytes]]:
        # The four keywords mirror the stdlib factory being fault-injected.
        nonlocal calls
        calls += 1
        with named_temp(dir=dir, delete=delete, prefix=prefix, suffix=suffix) as stream:
            write = stream.write

            def fail_write(payload: bytes) -> int:
                write(payload[:10])
                raise OSError(errno.ENOSPC, "injected temp write failure")

            if calls == 2:
                monkeypatch.setattr(stream, "write", fail_write)
            yield stream

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", fail_temp)
    # When applying the project migration.
    status = main([str(tmp_path), "--force", "--yes"])
    # Then even the earlier prepared file remains original, with no temp leaks.
    assert status == 1
    assert [path.read_bytes() for path in paths] == [original] * 2
    assert sorted(tmp_path.iterdir()) == paths


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX resource limits")
def test_preserves_original_when_file_size_limit_blocks_write(tmp_path: Path) -> None:
    # Given a real 3 KB source and a subprocess limited to 1 KB file writes.
    pytest.importorskip("resource")
    source = tmp_path / "app.py"
    original = (
        b"#" + b"x" * 3000 + b"\nfrom daytona import Daytona\nDaytona().create()\n"
    )
    source.write_bytes(original)
    requirements = tmp_path / "requirements.txt"
    requirements.write_bytes(b"daytona==0.224.0\n")
    # Import everything the run needs before lowering the limit, and never write
    # bytecode under it: a first-time import would otherwise leave a truncated
    # .pyc in the interpreter's shared stdlib cache for every later process.
    driver = (
        "import resource, signal, sys, encodings.utf_8_sig, encodings.latin_1; "
        "sys.dont_write_bytecode = True; "
        "from daytona_upgrade.cli import main; "
        "signal.signal(signal.SIGXFSZ, signal.SIG_IGN); "
        "resource.setrlimit(resource.RLIMIT_FSIZE, (1024, 1024)); "
        "sys.exit(main([sys.argv[1], '--force', '--yes']))"
    )
    # When running the CLI against the real filesystem, without mocked writes.
    result = subprocess.run(
        [sys.executable, "-B", "-c", driver, str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    # Then a failed write leaves both originals intact without leftover temps.
    assert result.returncode == 1
    assert source.read_bytes() == original
    assert requirements.read_bytes() == b"daytona==0.224.0\n"
    assert sorted(tmp_path.iterdir()) == [source, requirements]


def test_preserves_permissions_when_replacing_file(tmp_path: Path) -> None:
    # Given an executable source.
    path = tmp_path / "app.py"
    path.write_bytes(b"original")
    path.chmod(0o751)
    # When committing a replacement.
    write_outputs([(path, b"replacement")])
    # Then replacement preserves its original permission bits.
    assert path.read_bytes() == b"replacement"
    assert path.stat().st_mode & 0o777 == 0o751
    assert list(tmp_path.iterdir()) == [path]
