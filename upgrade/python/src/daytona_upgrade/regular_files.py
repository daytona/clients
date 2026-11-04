# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Bounded reads of regular project inputs, never pipes or devices."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from typing import Final

INPUT_LIMIT: Final = 5 * 1024 * 1024
DISCOVERY_LIMIT: Final = 64 * 1024 * 1024


def read_regular_bytes(path: Path, limit: int = INPUT_LIMIT) -> bytes:
    """Validate the opened descriptor without blocking on a swapped-in FIFO."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(errno.EINVAL, "not a regular file", str(path))
        if metadata.st_size > limit:
            raise OSError(errno.EFBIG, "file exceeds scan byte limit", str(path))
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise OSError(errno.EFBIG, "file exceeds scan byte limit", str(path))
    return data


def read_environment_text(path: Path) -> str:
    """Use one byte budget for every explicitly loaded deployment input."""
    return read_regular_bytes(path).decode("utf-8-sig")
