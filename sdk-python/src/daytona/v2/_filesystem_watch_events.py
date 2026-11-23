# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Boundary parsing for filesystem watch data events."""

from typing import Final, TypeVar

from ._core import finite_number, sse_json
from .errors import DaytonaError, create_error
from .types import FilesystemEvent, JsonObject

_T = TypeVar("_T")
_OVERFLOW_EVENT: Final = "filesystem watch overflow event"


def check_overflow_event(data: str) -> None:
    """Validate overflow even when no callback handles it."""
    dropped = sse_json("overflow", data, _OVERFLOW_EVENT).get("dropped")
    if not isinstance(dropped, bool):
        problem = "is required" if dropped is None else "must be a boolean"
        raise create_error(f"expected {_OVERFLOW_EVENT}: dropped {problem}", 400, "BAD_REQUEST", "SDK")


def parse_filesystem_event(payload: JsonObject) -> FilesystemEvent:
    def field(name: str, kind: type[_T], expected: str) -> _T:
        value = payload.get(name)
        # An int a double cannot carry is refused, as JSON.parse reads it as
        # Infinity.
        if (
            not isinstance(value, kind)
            or (kind is int and isinstance(value, bool))
            or (kind is int and not finite_number(value))
        ):
            problem = "is required" if value is None else f"must be {expected}"
            raise create_error(
                f"expected filesystem watch event: {name} {problem}", 400, "BAD_REQUEST", "SDK"
            )
        return value

    return FilesystemEvent(
        type=field("type", str, "a string"),
        name=field("name", str, "a string"),
        is_dir=field("isDir", bool, "a boolean"),
        timestamp_ms=field("timestamp", int, "an integer"),
    )


def parse_watch_error(data: str) -> DaytonaError:
    """Map a terminal server error like a process log stream's error event."""
    payload = sse_json("error", data, "filesystem watch error event")
    message = payload.get("message")
    if not isinstance(message, str):
        raise create_error(
            "expected filesystem watch error event: message must be a string", 400, "BAD_REQUEST", "SDK"
        )
    code = payload.get("code")
    source = payload.get("source")
    return create_error(
        message,
        None,
        code if isinstance(code, str) else None,
        source if isinstance(source, str) else "DAYTONA_DAEMON",
    )
