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


def boolean_option(value: object, name: str) -> bool:
    """A boolean option, refused (never truth-tested) otherwise."""
    if not isinstance(value, bool):
        raise create_error(f"{name} must be a boolean", 400, "BAD_REQUEST", "SDK")
    return value


def transfer_options(cancel: object, on_progress: object) -> None:
    """A file transfer's ``cancel`` and ``on_progress``, checked before any I/O:
    ``cancel`` is any object with a callable ``is_set()`` (``threading.Event``,
    ``asyncio.Event``, ...), ``on_progress`` a callable; ``None`` omits either."""
    if cancel is not None and not callable(getattr(cancel, "is_set", None)):
        raise create_error(
            "cancel must have an is_set() method, such as a threading.Event or an asyncio.Event",
            400,
            "BAD_REQUEST",
            "SDK",
        )
    if on_progress is not None and not callable(on_progress):
        raise create_error("on_progress must be callable", 400, "BAD_REQUEST", "SDK")


def buffer_bytes(value: Buffer, name: str) -> bytes:
    """Copy the bytes exposed by any buffer, including non-contiguous views."""
    try:
        with memoryview(value) as view:
            return bytes(view)
    except (TypeError, ValueError) as error:
        raise create_error(f"{name} must be bytes-like", 400, "BAD_REQUEST", "SDK") from error
