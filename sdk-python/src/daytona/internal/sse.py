# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ServerSentEvent:
    event: str
    data: str


@dataclass(slots=True)
class ServerSentEventParser:
    _event: str | None = None
    _data_lines: list[str] = field(default_factory=list)

    def feed_line(self, line: str) -> ServerSentEvent | None:
        normalized_line = line.rstrip("\r\n")
        if normalized_line == "":
            if self._event is None and not self._data_lines:
                return None
            event = ServerSentEvent(event=self._event or "message", data="\n".join(self._data_lines))
            self._event = None
            self._data_lines.clear()
            return event

        if normalized_line.startswith(":"):
            return None

        field_name, separator, value = normalized_line.partition(":")
        if separator == "":
            return None
        if value.startswith(" "):
            value = value[1:]

        if field_name == "event":
            self._event = value
            return None
        if field_name == "data":
            self._data_lines.append(value)
            return None
        return None

    def finalize(self) -> ServerSentEvent | None:
        if self._event is None and not self._data_lines:
            return None
        event = ServerSentEvent(event=self._event or "message", data="\n".join(self._data_lines))
        self._event = None
        self._data_lines.clear()
        return event
