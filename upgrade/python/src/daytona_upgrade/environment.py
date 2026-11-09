# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Read-only environment reports and literal endpoint refusal evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Final

from .config_messages import ENV_CUSTOM_URL
from .docker_environment import docker_environment
from .environment_values import endpoint_value
from .models import Item
from .yaml_flow import (
    YamlFlow,
    yaml_block_scalar,
    yaml_block_scalar_bodies,
    yaml_continuation,
)

VARIABLES: Final = frozenset({"DAYTONA_API_URL", "DAYTONA_SERVER_URL"})
_VARIABLE: Final = re.compile(r"(?<![\w$])DAYTONA_(?:API|SERVER)_URL\b")
_CLOUD: Final = re.compile(r"(?i:https://app\.daytona\.io)/api/?", re.ASCII)
_REPORT: Final = "{name} is ignored by SDK 2.x: .v0 targets Daytona Cloud, and .v2 reads only DAYTONA_V2_API_URL, for test environments. Review this setting."
_UNKNOWN_REPORT: Final = (
    "an environment write may set DAYTONA_API_URL or DAYTONA_SERVER_URL; "
    "check that it does not point at a custom API"
)


# Python 3.9's dataclass decorator does not support slots.
@dataclass(frozen=True)
class EnvironmentWrite:
    """An observed environment location, never copied into report messages."""

    file: str
    line: int
    column: int
    literal: str | None
    name: str | None = None

    def items(self, drop_api_url: bool) -> tuple[Item, ...]:
        """Always preserve manual review; refuse only a proven custom endpoint."""
        if self.literal == "":
            return ()
        report = Item(
            file=self.file,
            line=self.line,
            column=self.column,
            kind="env-api-url",
            message=_REPORT.format(name=self.name) if self.name else _UNKNOWN_REPORT,
        )
        if (
            not drop_api_url
            and self.literal is not None
            and _CLOUD.fullmatch(self.literal) is None
        ):
            return report, Item(
                file=self.file,
                line=self.line,
                column=self.column,
                kind="custom-api-url",
                message=ENV_CUSTOM_URL,
            )
        return (report,)


def _scalar(value: str, *, yaml: bool = False, flow: bool = False) -> str | None:
    """Recognize quoted or plain single-line values, without evaluating templates."""
    scalar = re.sub(r"^(?:\s*[&!]\S+\s+)+", "", value) if yaml else value
    if not scalar.strip() or scalar.lstrip().startswith("#"):
        return ""
    plain = r"""(?:\$\{[^}]*\}|[^\s,}\]"'])+""" if flow else r"""[^\s"']+"""
    match = re.match(rf"""\s*(?:"([^"]*)"|'([^']*)'|({plain}))""", scalar)
    literal = (
        next((group for group in match.groups() if group is not None), None)
        if match
        else None
    )
    if (
        match
        and match[3]
        and (
            match[3].startswith(("*", "&", "!", "|", ">"))
            or match[3].lower() in {"null", "~"}
        )
    ):
        return None
    return endpoint_value(literal) if literal is not None else None


def scan_environment(source: str, file: str) -> tuple[EnvironmentWrite, ...]:
    """Keep the existing name report; additionally recognize common deployment setters."""
    if PurePath(file).name in {".env.example", ".env.sample", ".env.template"}:
        return ()
    if PurePath(file).name.startswith("Dockerfile"):
        return tuple(
            EnvironmentWrite(file, write.line, write.column, write.literal, write.name)
            for write in docker_environment(source)
        )
    lines = source.splitlines()
    yaml = PurePath(file).suffix in {".yaml", ".yml"}
    writes: list[EnvironmentWrite] = []
    yaml_flow = YamlFlow()
    block_bodies = yaml_block_scalar_bodies(lines) if yaml else ()
    for number, line in enumerate(lines):
        comment = next(
            (
                token.start()
                for token in re.finditer(
                    r""""(?:\\.|[^"\\])*"|'[^']*'|(?P<comment>(?<!\S)\#)""", line
                )
                if token.group("comment")
            ),
            len(line),
        )
        flow = yaml_flow.line(line[:comment]) if yaml else ()
        for match in _VARIABLE.finditer(line[:comment]):
            # References in values (including interpolation operator colons) are
            # not declarations. Preserve shell setters and Kubernetes name keys.
            prefix = line[: match.start()]
            if prefix.endswith("${"):
                continue
            if not (
                re.search(r"""(?:^|[\s\[{},;])(?:export\s+)?["']?$""", prefix)
                or re.search(r"""\bname\s*:\s*["']?$""", prefix)
            ):
                continue
            literal = None
            suffix = line[match.end() : comment]
            in_block_body = bool(block_bodies and block_bodies[number])
            if in_block_body and not re.match(r"\s*=", suffix):
                continue
            assignment = re.match(r"""["']?\s*[:=]\s*(.*)""", suffix)
            if assignment:
                value = assignment[1]
                ambiguous_continuation = False
                is_block, block_value = (
                    yaml_block_scalar(lines, number, line) if yaml else (False, None)
                )
                if is_block:
                    literal = _scalar(block_value, yaml=True) if block_value else None
                elif yaml and not value.strip():
                    indent = len(line) - len(line.lstrip())
                    continuation, ambiguous_continuation = yaml_continuation(
                        lines, number + 1, indent
                    )
                    if continuation is not None:
                        value = continuation
                quote = (
                    re.search(
                        r"""(?:^\s*(?:-\s*)?|[\[,]\s*)(["'])$""", line[: match.start()]
                    )
                    if yaml and re.match(r"""["']?\s*=""", suffix)
                    else None
                )
                if not is_block:
                    scalar = (quote[1] if quote else "") + value
                    literal = (
                        None
                        if ambiguous_continuation
                        else _scalar(
                            scalar, yaml=yaml, flow=bool(flow and flow[match.start()])
                        )
                    )
            elif re.fullmatch(r"\s*ENV\s+", line[: match.start()], re.I):
                literal = _scalar(suffix, yaml=yaml)
            elif re.search(r"""\bname\s*:\s*["']?$""", line[: match.start()]):
                # Kubernetes name/value objects, including YAML flow mappings.
                tail = re.match(r"""["']?\s*,\s*["']?value["']?\s*:\s*(.*)""", suffix)
                if tail:
                    literal = _scalar(
                        tail[1], yaml=yaml, flow=bool(flow and flow[match.start()])
                    )
                else:
                    indent = len(line) - len(line.lstrip())
                    for following in lines[number + 1 :]:
                        if not following.strip() or following.lstrip().startswith("#"):
                            continue
                        if len(following) - len(following.lstrip()) <= indent:
                            break
                        value = re.match(r"\s*value\s*:\s*(.*)", following)
                        if value:
                            is_block, block_value = yaml_block_scalar(
                                lines, number + 1, following
                            )
                            if is_block:
                                literal = (
                                    _scalar(block_value, yaml=True)
                                    if block_value
                                    else None
                                )
                            elif not value[1].strip():
                                continuation, ambiguous = yaml_continuation(
                                    lines,
                                    number + 2,
                                    len(following) - len(following.lstrip()),
                                )
                                literal = (
                                    None
                                    if ambiguous
                                    else _scalar(continuation or "", yaml=True)
                                )
                            else:
                                literal = _scalar(value[1], yaml=yaml)
                        else:
                            literal = None
                        break
            if literal != "":
                writes.append(
                    EnvironmentWrite(
                        file, number + 1, match.start() + 1, literal, match[0]
                    )
                )
    return tuple(writes)
