# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Dockerfile environment declaration discovery."""

from __future__ import annotations

import re
import shlex
from typing import Final, NamedTuple

from .environment_values import endpoint_value

_VARIABLE: Final = re.compile(r"\bDAYTONA_(?:API|SERVER)_URL\b")
_VARIABLES: Final = frozenset({"DAYTONA_API_URL", "DAYTONA_SERVER_URL"})


class DockerEnvironmentWrite(NamedTuple):
    """Source location and literal evidence for one Docker declaration."""

    line: int
    column: int
    literal: str | None
    name: str


def docker_environment(source: str) -> tuple[DockerEnvironmentWrite, ...]:
    """Read ENV/ARG declaration keys, excluding shell text and heredoc payloads."""
    writes: list[DockerEnvironmentWrite] = []
    lines = source.splitlines()
    number = 0
    while number < len(lines):
        start = number
        line = lines[number]
        number += 1
        if line.lstrip().startswith("#"):
            continue
        while line.endswith("\\") and number < len(lines):
            if not lines[number].lstrip().startswith("#"):
                line = line[:-1] + lines[number]
            number += 1
        bare = re.match(r"\s*(DAYTONA_(?:API|SERVER)_URL)\s*=", line)
        if bare:
            # This ENV syntax is report-only, not runtime value evidence.
            writes.append(
                DockerEnvironmentWrite(start + 1, bare.start(1) + 1, None, bare[1])
            )
            continue
        instruction = re.match(r"\s*(?:ONBUILD\s+)?(\w+)\s+(.*)", line, re.I)
        if instruction is None:
            continue
        command, arguments = instruction[1].upper(), instruction[2]
        if command in {"RUN", "COPY", "ADD"}:
            payload_end = number
            for token in re.finditer(
                r""""(?:\\.|[^"\\])*"|'[^']*'|(?<!<)<<(?P<tabs>-?)\s*(?:"(?P<double>[^"\\]+)"|'(?P<single>[^']+)'|(?P<plain>[\w.-]+))(?=\s|$|[;&|])""",
                arguments,
            ):
                delimiter = token["double"] or token["single"] or token["plain"]
                if delimiter:
                    terminator = next(
                        (
                            index
                            for index in range(payload_end, len(lines))
                            if (
                                lines[index].lstrip("\t")
                                if token["tabs"]
                                else lines[index]
                            )
                            == delimiter
                        ),
                        None,
                    )
                    if terminator is None:
                        payload_end = number
                        break
                    payload_end = terminator + 1
            number = payload_end
        declaration = re.match(r"\s*(?:ONBUILD\s+)?(ENV|ARG)\s+(.*)", line, re.I)
        if declaration is None:
            continue
        command, arguments = declaration[1].upper(), declaration[2]
        try:
            tokens = shlex.split(arguments)
        except ValueError:
            writes.extend(
                DockerEnvironmentWrite(start + 1, match.start() + 1, None, match[0])
                for match in _VARIABLE.finditer(line)
            )
            continue
        if not tokens:
            continue
        pairs = (
            [tokens[0] + "=" + " ".join(tokens[1:])]
            if command == "ENV" and "=" not in tokens[0]
            else tokens
        )
        for index, pair in enumerate(pairs):
            key, separator, value = pair.partition("=")
            if key not in _VARIABLES:
                continue
            if (
                separator
                and not value
                and index + 1 < len(pairs)
                and "=" not in pairs[index + 1]
            ):
                value = pairs[index + 1]
            literal = endpoint_value(value) if separator else None
            writes.append(
                DockerEnvironmentWrite(start + 1, line.find(key) + 1, literal, key)
            )
    return tuple(writes)
