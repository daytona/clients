# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Apply prepared file outputs with byte-preserving rollback on I/O failure."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def _target_message(error: OSError) -> str:
    return f"{error.filename} could not be written: {error.strerror or 'I/O error'}; fix the problem, then rerun."


def target_error(path: Path, error: OSError, base: Path) -> OSError:
    """Retain OS fields and category while displaying only the logical target."""
    relative = Path(os.path.relpath(path, base)).as_posix()
    # Subclass the actual category (PermissionError, FileNotFoundError, etc.) so
    # callers retain their existing catches without OSError's errno-prefixed display.
    wrapper: type[OSError] = type(
        "TargetWriteError", (type(error),), {"__str__": _target_message}
    )
    return wrapper(error.errno, error.strerror, relative)


def _prepare(path: Path, payload: bytes, temporaries: list[Path]) -> Path:
    """Stage complete bytes beside the target and track cleanup before I/O."""
    with tempfile.NamedTemporaryFile(
        dir=path.parent,
        delete=False,
        prefix=f".{path.name}.",
        suffix=".daytona-upgrade.tmp",
    ) as stream:
        temporary = Path(stream.name)
        temporaries.append(temporary)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    if path.exists():
        shutil.copymode(path, temporary)
    return temporary


def write_outputs(payloads: list[tuple[Path, bytes]], root: Path | None = None) -> None:
    """Prepare every output before replacing targets; atomically undo I/O failures."""
    if not payloads:
        return
    path = payloads[0][0]
    base = root or Path.cwd()
    originals: dict[Path, bytes | None] = {}
    for path, _ in payloads:
        try:
            originals[path] = path.read_bytes() if path.exists() else None
        except OSError as error:
            raise target_error(path, error, base) from error
    replaced: list[Path] = []
    temporaries: list[Path] = []
    try:
        staged: list[tuple[Path, Path]] = []
        for path, payload in payloads:
            staged.append((path, _prepare(path, payload, temporaries)))
        for path, temporary in staged:
            os.replace(temporary, path)
            replaced.append(path)
    except OSError as error:
        failures: list[str] = []
        for previous in reversed(replaced):
            original = originals[previous]
            try:
                if original is None:
                    previous.unlink(missing_ok=True)
                else:
                    os.replace(_prepare(previous, original, temporaries), previous)
            except OSError as rollback_error:
                failures.append(
                    f"{Path(os.path.relpath(previous, base)).as_posix()}: {rollback_error.strerror or 'I/O error'}"
                )
        leftovers: list[str] = []
        for temporary in temporaries:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                leftovers.append(Path(os.path.relpath(temporary, base)).as_posix())
        reason = error.strerror or "I/O error"
        if failures:
            reason += f"; rollback failed for: {'; '.join(failures)}"
        if leftovers:
            reason += f"; leftover temp files: {'; '.join(leftovers)}"
        raise target_error(path, OSError(error.errno, reason), base) from error
