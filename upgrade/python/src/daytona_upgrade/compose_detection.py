# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Cheap Compose candidate filtering before bounded YAML tokenization."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from .compose_includes import ComposeInclude, include_entries
from .compose_yaml import has_services, literal_path, root_flow_start

_TOP_LEVEL: Final = re.compile(
    r"""^(?:services|include|"(?:services|include)"|'(?:services|include)')\s*:""",
    re.M,
)
_FLOW_PROBE: Final = re.compile(
    r""""(?:\\.|[^"\\])*"|'(?:''|[^'])*'|[{}\[\]]|\b(?:services|include)\s*:"""
)


def _flow_candidate(source: str) -> bool:
    """Probe root flow keys with constant-memory iteration, not a YAML token list."""
    start = root_flow_start(source)
    if start is None:
        return False
    depth = 0
    for token in _FLOW_PROBE.finditer(source, start):
        word = token[0]
        if word in {"{", "["}:
            depth += 1
        elif word in {"}", "]"}:
            depth -= 1
        elif depth == 1 and (
            word.rstrip().rstrip(":").strip() in {"services", "include"}
            or literal_path(word) in {"services", "include"}
            and re.match(r"\s*:", source[token.end() : token.end() + 128])
        ):
            return True
    return False


def compose_candidate(path: Path, source: str) -> bool:
    """Avoid tokenizing unrelated YAML, including large generated OpenAPI specs."""
    return path.name != ".gitlab-ci.yml" and (
        path.name.startswith(("compose", "docker-compose"))
        or bool(_TOP_LEVEL.search(source))
        # Preserve root flow collections and escaped/indented YAML keys.
        or _flow_candidate(source)
        or bool(re.search(r"""^[ \t]*["'][^\n]*\\[^\n]*["']\s*:""", source, re.M))
        or bool(re.search(r"^\s+(?:services|include)\s*:", source, re.M))
    )


def is_compose(path: Path, source: str) -> bool:
    """Require Compose filenames, services, or include entries with Compose keys."""
    if not compose_candidate(path, source):
        return False
    if path.name.startswith(("compose", "docker-compose")) or has_services(source):
        return True
    # Every real entry must look like Compose: one Compose-shaped entry in a CI list is not
    # enough. A bare `(None,)` marker only records alias/tag uncertainty about the value.
    entries = [
        entry for entry in include_entries(source) if entry != ComposeInclude((None,))
    ]
    return bool(entries) and all(entry.compose_keys for entry in entries)
