# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Read literal Compose env_file inputs without evaluating YAML templates."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

from .compose_includes import include_entries
from .compose_projects import ComposeContext as ComposeContext
from .compose_yaml import (
    _TOKEN,
    compose_text,
    flow_entries,
    flow_paths,
    literal_path,
    service_keys,
    uncertain_yaml,
)
from .environment import EnvironmentWrite, scan_environment
from .models import Item, TransformOptions
from .regular_files import read_environment_text
from .yaml_flow import _without_comment, yaml_block_scalar_bodies


def _entries(source: str) -> Iterator[tuple[int, int, str]]:
    """Locate block entries or inline scalars/lists, excluding block payloads."""
    lines = source.splitlines()
    text_source = compose_text(source)
    tokens = list(_TOKEN.finditer(text_source))
    loader_lines = {
        text_source.count("\n", 0, tokens[index].start()) + 1
        for index in service_keys(text_source, tokens)
    }
    bodies = yaml_block_scalar_bodies(lines)
    parent_indent: int | None = None
    for number, line in enumerate(lines, 1):
        if bodies[number - 1]:
            continue
        text = _without_comment(line)
        if not text.strip():
            continue
        indent = len(text) - len(text.lstrip())
        if parent_indent is not None:
            if indent > parent_indent or (
                indent == parent_indent and text.lstrip().startswith("- ")
            ):
                value = re.sub(r"^\s*-\s*", "", text).strip()
                yield number, indent + 1, value
                continue
            parent_indent = None
        field = re.match(
            r"""\s*("(?:\\.|[^"\\])*"|'(?:''|[^'])*'|env_file)\s*:(?=\s|$)\s*(.*)""",
            text,
        )
        if (
            number in loader_lines
            and field is not None
            and literal_path(field[1]) == "env_file"
        ):
            value = field[2].strip()
            if value.startswith(("[", "{")):
                continue  # The bounded tokenizer owns complete flow collections.
            if value:
                yield number, field.start(2) + 1, value
            else:
                parent_indent = indent


def _paths(value: str) -> Iterator[str | None]:
    """Extract short-form paths and long-form path members, not required/format."""
    # Preserve quoted commas and interpolation expressions while splitting flow
    # collection delimiters. Other YAML constructs remain unresolved review.
    parts = re.findall(
        r"""(?:"(?:\\.|[^"\\])*"|'(?:''|[^'])*'|\$\{[^}]*\}|[^,\[\]{}])+""",
        value,
    )
    for part in parts:
        field = re.match(r"\s*(?:['\"]?(path|required|format)['\"]?)\s*:\s*(.*)", part)
        if field is not None:
            if field[1] != "path":
                continue
            part = field[2]
        literal = literal_path(part)
        if literal != "":
            yield literal


def scan_compose_environment(
    source: str, options: TransformOptions, context: ComposeContext
) -> tuple[Item, ...]:
    """Scan all existing env_file candidates; unresolved inputs retain review."""
    root, file = context.root, context.file
    items: list[Item] = []
    text = compose_text(source)
    entries = list(flow_entries(text))
    if uncertain_yaml(text):
        items.extend(EnvironmentWrite(file, 1, 1, None).items(options.drop_api_url))
    paths = [
        (line, column, literal)
        for line, column, value in entries
        for literal in flow_paths(value)
    ]
    for line, column, value in _entries(source):
        for literal in _paths(value):
            paths.append((line, column, literal))
    for include in include_entries(source):
        paths.extend((1, 1, literal) for literal in include.env_files)
    for line, column, literal in paths:
        review = EnvironmentWrite(file, line, column, None)
        if literal is None or root is None:
            items.extend(review.items(options.drop_api_url))
            continue
        candidates = sorted(
            {
                directory / literal
                for directory in (root, (root / file).parent, *context.directories)
            }
        )
        found = False
        for path in candidates:
            try:
                loaded = read_environment_text(path)
            except FileNotFoundError:
                continue
            except (OSError, UnicodeError):
                items.extend(review.items(options.drop_api_url))
                continue
            found = True
            relative = Path(os.path.relpath(path, root)).as_posix()
            items.extend(
                item
                for write in scan_environment(loaded, ".env")
                for item in replace(write, file=relative).items(options.drop_api_url)
            )
        if not found:
            items.extend(review.items(options.drop_api_url))
    return tuple(items)
