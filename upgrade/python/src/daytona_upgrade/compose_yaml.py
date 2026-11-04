# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Bounded recognition of Compose keys and literal flow values, not general YAML."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Final

from .yaml_flow import YamlFlow, _without_comment, yaml_block_scalar_bodies

_TOKEN: Final = re.compile(
    r""""(?:\\.|[^"\\])*"|'(?:''|[^'])*'|\$\{[^}]*\}|[{},\[\]:]|[^\s{},\[\]:]+"""
)
_PREAMBLE_LINE: Final = re.compile(r"[ \t]*(?:---[ \t]*)?(?:#.*)?")


def root_flow_start(source: str) -> int | None:
    """Locate a root flow mapping past blank lines, comments and document markers."""
    # A line scan, not one regex over the whole preamble, so the match stays linear.
    offset = 0
    for line in source.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        stripped = body.lstrip(" \t")
        if stripped.startswith("{"):
            return offset + len(body) - len(stripped)
        if line == body or _PREAMBLE_LINE.fullmatch(body) is None:
            return None
        offset += len(line)
    return None


def compose_text(source: str) -> str:
    """Mask comments and block payloads while retaining diagnostic coordinates."""
    lines = source.splitlines(keepends=True)
    bodies = yaml_block_scalar_bodies(lines)
    return "".join(
        "".join("\n" if char == "\n" else " " for char in line)
        if body
        else _without_comment(line).rstrip("\r\n") + "\n"
        for line, body in zip(lines, bodies)
    )


def literal_path(value: str) -> str | None:
    """Decode only complete literal scalars; uncertain YAML stays review-only."""
    text = value.strip()
    if re.fullmatch(r"'(?:''|[^'])*'", text):
        literal = text[1:-1].replace("''", "'")
    elif re.fullmatch(r'"(?:\\.|[^"\\])*"', text):
        try:
            literal = json.loads(text)
        except ValueError:
            return None
        if not isinstance(literal, str):
            return None
    elif re.fullmatch(r"[^\s{},\[\]:\"']+", text) and not text.startswith(
        ("*", "&", "!", "|", ">", "?", "%")
    ):
        literal = text
    else:
        return None
    return (
        None
        if any(marker in literal for marker in ("$", "{{", "{%"))
        or literal.lower() in {"null", "~"}
        else literal
    )


def mapping_keys(text: str, tokens: list[re.Match[str]]) -> Iterator[tuple[int, int]]:
    """Yield key token indexes and flow depths, never keys embedded in scalars."""
    flow = YamlFlow()
    membership = [
        flag for line in text.splitlines(keepends=True) for flag in flow.line(line)
    ]
    depth = 0
    for index, token in enumerate(tokens[:-1]):
        word = token[0]
        prefix = text[text.rfind("\n", 0, token.start()) + 1 : token.start()]
        colon = tokens[index + 1]
        following = text[colon.end() : colon.end() + 1]
        if (
            colon[0] == ":"
            and (
                not following
                or following.isspace()
                or word.startswith(("'", '"'))
                or (depth > 0 and following in "[{'\"},]")
            )
            and (
                (depth == 0 and re.fullmatch(r"\s*(?:-\s+)*", prefix) is not None)
                or (depth > 0 and index > 0 and tokens[index - 1][0] in {"{", ",", "["})
            )
        ):
            yield index, depth
        if word in {"{", "["} and membership[token.end()]:
            depth += 1
        elif word in {"}", "]"} and membership[token.start()]:
            depth = max(0, depth - 1)


def has_services(source: str) -> bool:
    """Recognize top-level services mappings without classifying nested K8s data."""
    text = compose_text(source)
    tokens = list(_TOKEN.finditer(text))
    base_indent = min(
        (len(line) - len(line.lstrip()) for line in text.splitlines() if line.strip()),
        default=0,
    )
    for index, depth in mapping_keys(text, tokens):
        token = tokens[index]
        prefix = text[text.rfind("\n", 0, token.start()) + 1 : token.start()]
        if literal_path(token[0]) == "services" and (
            (depth == 0 and not prefix.strip() and len(prefix) == base_indent)
            or (depth == 1 and root_flow_start(text) is not None)
        ):
            tail = text[tokens[index + 1].end() :].lstrip(" \t")
            return tail.startswith(("{", "\n", "&", "*", "!"))
    return False


def service_keys(text: str, tokens: list[re.Match[str]]) -> Iterator[int]:
    """Recognize only services.<name>.env_file across block and flow mappings."""
    keys = dict(mapping_keys(text, tokens))
    blocks: list[tuple[int, tuple[str | None, ...]]] = []
    collections: list[tuple[str | None, ...]] = []
    value_path: tuple[str | None, ...] = ()
    flow = YamlFlow()
    membership = [
        flag for line in text.splitlines(keepends=True) for flag in flow.line(line)
    ]
    for index, token in enumerate(tokens):
        word = token[0]
        if index in keys:
            if not keys[index]:
                prefix = text[text.rfind("\n", 0, token.start()) + 1 : token.start()]
                indent = len(prefix)
                while blocks and blocks[-1][0] >= indent:
                    blocks.pop()
                parent = blocks[-1][1] if blocks else ()
                value_path = (*parent, literal_path(word))
                blocks.append((indent, value_path))
            else:
                value_path = (
                    *(collections[-1] if collections else ()),
                    literal_path(word),
                )
            if (
                len(value_path) == 3
                and value_path[0] == "services"
                and value_path[-1] == "env_file"
            ):
                yield index
        if word in {"{", "["} and membership[token.end()]:
            collections.append(value_path)
        elif word in {"}", "]"} and membership[token.start()] and collections:
            collections.pop()
            value_path = collections[-1] if collections else ()
        elif word == "," and collections:
            value_path = collections[-1]


def flow_entries(text: str) -> Iterator[tuple[int, int, str]]:
    """Locate mapping keys and balance flow values, with a 64-level depth budget."""
    tokens = list(_TOKEN.finditer(text))
    loaders = set(service_keys(text, tokens))
    for index, depth in mapping_keys(text, tokens):
        token = tokens[index]
        if index not in loaders:
            continue
        start = tokens[index + 1].end()
        tail = text[start:].lstrip()
        if not depth and not tail.startswith(("[", "{")):
            continue
        closers: list[str] = []
        end = start
        for value_index in range(index + 2, len(tokens)):
            value = tokens[value_index]
            word = value[0]
            if not closers and (
                word in {",", "}"} or "\n" in text[end : value.start()]
            ):
                break
            if word in {"[", "{"}:
                closers.append("]" if word == "[" else "}")
                if len(closers) > 64:
                    break
            elif word in {"]", "}"}:
                if not closers or word != closers.pop():
                    closers.append("@unresolved")
                    break
            end = value.end()
            if not closers and text[start:end].strip().startswith(("[", "{")):
                if value_index + 1 < len(tokens):
                    following = tokens[value_index + 1]
                    if (
                        following[0] not in {",", "}"}
                        and "\n" not in text[end : following.start()]
                    ):
                        closers.append("@unresolved")
                break
        line = text.count("\n", 0, token.start()) + 1
        column = token.start() - text.rfind("\n", 0, token.start())
        yield line, column, "@unresolved" if closers else text[start:end]


def flow_paths(value: str) -> Iterator[str | None]:
    """Accept scalar/list/path-map syntax; never certify unsupported collections."""
    text = value.strip()
    if not text.startswith(("[", "{")):
        yield literal_path(text)
        return
    pending = [text]
    # Each iteration consumes a strictly smaller collection; no recursive stack.
    while pending:
        entry = pending.pop().strip()
        if not entry.startswith(("[", "{")):
            yield literal_path(entry)
            continue
        if not entry.endswith("]" if entry.startswith("[") else "}"):
            yield None
            continue
        parts: list[str] = []
        start = 1
        depth = 0
        for token in _TOKEN.finditer(entry[1:-1]):
            word = token[0]
            if word == "," and depth == 0:
                parts.append(entry[start : token.start() + 1])
                start = token.end() + 1
            depth += word in {"[", "{"}
            depth -= word in {"]", "}"}
        parts.append(entry[start:-1])
        if entry.startswith("["):
            pending.extend(reversed([part for part in parts if part.strip()]))
            continue
        found = False
        for part in parts:
            field = re.fullmatch(
                r"\s*(path|required|format|'[^']*'|\"[^\"]*\")\s*:\s*(.+)\s*",
                part,
                re.S,
            )
            if field is None or literal_path(field[1]) not in {
                "path",
                "required",
                "format",
            }:
                yield None
            elif literal_path(field[1]) == "path":
                found = True
                yield literal_path(field[2])
        if not found:
            yield None


def uncertain_yaml(text: str) -> bool:
    """Advanced YAML can redirect loaders even when a literal candidate is clean."""
    tokens = list(_TOKEN.finditer(text))
    keys = [tokens[index][0] for index, _ in mapping_keys(text, tokens)]
    if any(key.startswith("*") for key in keys):
        return True
    if not set(service_keys(text, tokens)) and not (
        "<<" in keys and any(literal_path(key) == "env_file" for key in keys)
    ):
        return False
    return any(
        token[0].startswith(("&", "*", "!", "%")) or token[0] in {"<<", "---", "..."}
        for token in tokens
    )
