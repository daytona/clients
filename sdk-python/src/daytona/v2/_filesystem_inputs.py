# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from collections.abc import Sequence

from typing_extensions import Buffer

from .errors import create_error


def sandbox_path(value: str, name: str = "path") -> str:
    """Parse an absolute sandbox path without resolving or normalizing it."""
    if not isinstance(value, str) or not value.startswith("/"):
        raise create_error(f"{name} must be an absolute path (starting with '/')", 400, "BAD_REQUEST", "SDK")
    return value


def path_sequence(value: Sequence[str], name: str) -> list[str]:
    """Snapshot a path sequence, refusing scalar strings and non-string items."""
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise create_error(f"{name} must be a sequence of strings", 400, "BAD_REQUEST", "SDK")
    result = list(value)
    if not all(isinstance(item, str) for item in result):
        raise create_error(f"{name} must be a sequence of strings", 400, "BAD_REQUEST", "SDK")
    return result


def buffer_bytes(value: Buffer, name: str) -> bytes:
    """Copy the bytes exposed by any buffer, including non-contiguous views."""
    try:
        with memoryview(value) as view:
            return bytes(view)
    except (TypeError, ValueError) as error:
        raise create_error(f"{name} must be bytes-like", 400, "BAD_REQUEST", "SDK") from error
