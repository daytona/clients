# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Bounded literal Compose include parsing, without evaluating YAML."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from .compose_yaml import (
    _TOKEN,
    compose_text,
    literal_path,
    mapping_keys,
    root_flow_start,
)


# Python 3.9's dataclass decorator does not support slots.
@dataclass(frozen=True)
class ComposeInclude:
    """Literal include inputs plus explicit uncertainty at the YAML boundary."""

    paths: tuple[str | None, ...]
    env_files: tuple[str | None, ...] = ()
    project_directory: str | None = None
    uncertain: bool = False
    compose_keys: bool = False


def _flow_parts(value: str) -> tuple[str, ...] | None:
    """Split one balanced flow collection; reject trailing or malformed syntax."""
    closers: list[str] = []
    parts: list[str] = []
    start = 1
    tokens = list(_TOKEN.finditer(value))
    for index, token in enumerate(tokens):
        word = token[0]
        if word in {"[", "{"}:
            closers.append("]" if word == "[" else "}")
            if len(closers) > 64:
                return None
        elif word in {"]", "}"}:
            if not closers or word != closers.pop():
                return None
            if not closers:
                if index != len(tokens) - 1:
                    return None
                last = value[start : token.start()].strip()
                if last:
                    parts.append(last)
                return tuple(parts)
        elif word == "," and len(closers) == 1:
            part = value[start : token.start()].strip()
            if not part:
                return None
            parts.append(part)
            start = token.end()
    return None


def _sequence(value: str) -> tuple[str, ...] | None:
    """Recognize a flow list or block sequence, preserving each item's body."""
    text = value.strip()
    if text.startswith("["):
        return _flow_parts(text)
    lines = [line for line in value.splitlines() if line.strip()]
    if not lines:
        return None
    indent = len(lines[0]) - len(lines[0].lstrip())
    groups: list[str] = []
    for line in lines:
        prefix = line[:indent]
        tail = line[indent:]
        if not prefix.strip() and tail.startswith("- "):
            groups.append(tail[2:] + "\n")
        elif groups and len(line) - len(line.lstrip()) > indent:
            groups[-1] += line[indent + 2 :] + "\n"
        else:
            return None
    return tuple(groups)


def _paths(value: str) -> tuple[str | None, ...]:
    """Only scalar paths and one-dimensional literal path lists are accepted."""
    parts = _sequence(value)
    return (
        tuple(literal_path(part) for part in parts)
        if parts is not None
        else (literal_path(value),)
    )


def _fields(value: str) -> tuple[tuple[str | None, str], ...] | None:
    """Read the three long-form fields from a flow or block mapping."""
    text = value.strip()
    if text.startswith("{"):
        parts = _flow_parts(text)
        if parts is None:
            return None
    else:
        parts_list: list[str] = []
        for line in value.splitlines():
            if not line.strip():
                continue
            if not line[0].isspace():
                parts_list.append(line)
            elif parts_list:
                parts_list[-1] += "\n" + line
            else:
                return None
        parts = tuple(parts_list)
    fields: list[tuple[str | None, str]] = []
    for part in parts:
        field = re.fullmatch(
            r"""\s*("(?:\\.|[^"\\])*"|'(?:''|[^'])*'|[^\s:]+)\s*:(?=\s|$|[\[{'"])(.*)""",
            part,
            re.S,
        )
        if field is None:
            return None
        fields.append((literal_path(field[1]), field[2]))
    return tuple(fields)


def _include(value: str) -> ComposeInclude:
    """Parse an include entry while retaining uncertainty alongside candidates."""
    fields = _fields(value)
    if fields is None:
        path = literal_path(value)
        # An interpolated scalar path (`${MANIFEST}`) is still a Compose include, just unresolved;
        # alias-only entries stay unclassified.
        interpolated = path is None and value.strip().strip("'\"").startswith("${")
        return ComposeInclude(
            (path,),
            uncertain=interpolated,
            compose_keys=path is not None or interpolated,
        )
    names = [key for key, _ in fields]
    values = {key: body for key, body in fields}
    directory = (
        literal_path(values["project_directory"])
        if "project_directory" in values
        else None
    )
    return ComposeInclude(
        _paths(values.get("path", "")),
        _paths(values["env_file"]) if "env_file" in values else (),
        directory,
        len(names) != len(set(names))
        or any(key not in {"path", "env_file", "project_directory"} for key in names)
        or "path" not in values
        or ("project_directory" in values and directory is None),
        bool(names)
        and all(key in {"path", "env_file", "project_directory"} for key in names),
    )


def include_entries(source: str) -> Iterator[ComposeInclude]:
    """Locate only top-level include keys, never comments or scalar payloads."""
    if "include" not in source and "\\" not in source:
        return
    text = compose_text(source)
    tokens = list(_TOKEN.finditer(text))
    keys = list(mapping_keys(text, tokens))
    found = False
    base_indent = min(
        (len(line) - len(line.lstrip()) for line in text.splitlines() if line.strip()),
        default=0,
    )
    for index, depth in keys:
        token = tokens[index]
        prefix = text[text.rfind("\n", 0, token.start()) + 1 : token.start()]
        if literal_path(token[0]) != "include" or not (
            (depth == 0 and not prefix.strip() and len(prefix) == base_indent)
            or (depth == 1 and root_flow_start(text) is not None)
        ):
            continue
        found = True
        start = tokens[index + 1].end()
        tail = text[start:]
        if tail.lstrip().startswith("["):
            closers: list[str] = []
            end = len(text)
            for value_token in tokens[index + 2 :]:
                word = value_token[0]
                if word in {"[", "{"}:
                    closers.append("]" if word == "[" else "}")
                elif word in {"]", "}"}:
                    if not closers or word != closers.pop():
                        break
                    if not closers:
                        end = value_token.end()
                        following = text[end:].split("\n", 1)[0]
                        if following.strip() and not (
                            depth and following.lstrip().startswith((",", "}"))
                        ):
                            end += len(following)
                        break
            value = text[start:end].strip()
        elif tail.split("\n", 1)[0].strip() or depth:
            value = tail.split("\n", 1)[0].strip()
        else:
            lines: list[str] = []
            for line in tail.splitlines()[1:]:
                indent = len(line) - len(line.lstrip())
                if (
                    line.strip()
                    and indent <= base_indent
                    and not (indent == base_indent and line.lstrip().startswith("- "))
                ):
                    break
                lines.append(line)
            value = "\n".join(lines)
        if any(
            token[0].startswith(("&", "*", "!", "%"))
            or token[0] in {"<<", "---", "..."}
            for token in _TOKEN.finditer(value)
        ):
            yield ComposeInclude((None,))
        entries = _sequence(value)
        if entries is None:
            yield ComposeInclude((None,))
        else:
            for entry in entries:
                yield _include(entry)
    words = [tokens[index][0] for index, _ in keys]
    if (
        not found
        and "<<" in words
        and any(literal_path(word) == "include" for word in words)
    ) and any(
        token[0].startswith(("&", "*", "!", "%")) or token[0] in {"<<", "---", "..."}
        for token in tokens
    ):
        yield ComposeInclude((None,))
