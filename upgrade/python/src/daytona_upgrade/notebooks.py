# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Notebook code-cell adapter, with opaque metadata and reversible magic masking."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .dependency_versions import target_constraint
from .lexical import assignment_magic_prefix, magic_lines, source_lines
from .models import Item, SourceResult, TransformOptions
from .notebook_environment import magic_environment
from .notebook_json import cell_source_spans, rewrite_source_value

if TYPE_CHECKING:
    from .environment import EnvironmentWrite
    from .flow import SourceContext


@dataclass(frozen=True)
class CodeCell:
    index: int
    source: str


@dataclass(frozen=True)
class Notebook:
    original: str
    cells: tuple[CodeCell, ...]
    marker: str
    combined: str
    magics: tuple[tuple[str, str], ...]
    environment: tuple[EnvironmentWrite, ...]


@dataclass(frozen=True)
class NotebookError(ValueError):
    message: str

    def __str__(self) -> str:
        return self.message


def notebook_failure(file: str, error: ValueError) -> Item:
    """Report a notebook decoding failure without aborting project preflight."""
    return Item(
        file=file,
        line=0,
        column=0,
        kind="unsupported-file",
        message=f"{file} is not a valid notebook: {error}; fix or remove it, then rerun.",
    )


def read_notebook(source: str) -> Notebook:
    """Decode code cells, joining them in execution order for flow analysis."""
    raw = json.loads(source)
    if not isinstance(raw, dict) or not isinstance(raw.get("cells"), list):
        raise NotebookError("Notebook must contain a cells array")
    cells_list: list[CodeCell] = []
    for index, cell in enumerate(raw["cells"]):
        if not isinstance(cell, dict):
            raise NotebookError(f"Notebook cell {index + 1} must be an object")
        if cell.get("cell_type") != "code":
            continue
        text = cell.get("source", [])
        if not isinstance(text, (str, list)) or (
            isinstance(text, list) and not all(isinstance(line, str) for line in text)
        ):
            raise NotebookError(
                f"Notebook cell {index + 1} source must be text or an array of text"
            )
        cells_list.append(CodeCell(index, "".join(text)))
    cells = tuple(cells_list)
    marker = "# __daytona_upgrade_cell_"
    while any(marker in cell.source for cell in cells):
        marker += "_"
    combined: list[str] = []
    magics: list[tuple[str, str]] = []
    environment: list[EnvironmentWrite] = []
    for cell in cells:
        combined.append(f"{marker}{cell.index}__\n")
        first = cell.source.lstrip().splitlines()
        magic = first[0].split()[0] if first else ""
        cell_magic = magic.startswith("%%") and magic not in {
            "%%time",
            "%%timeit",
            "%%capture",
            "%%prun",
            "%%python",
            "%%python3",
            "%%debug",
        }
        executable_magics = magic_lines(cell.source) if not cell_magic else frozenset()
        for number, line in enumerate(source_lines(cell.source), 1):
            if cell_magic or number in executable_magics:
                if not cell_magic:
                    write = magic_environment(line, len(combined) + 1)
                    if write is not None:
                        environment.append(write)
                token = f"{marker}magic_{len(magics)}__"
                indent = (
                    "" if cell_magic else line[: len(line) - len(line.lstrip(" \t"))]
                )
                prefix = assignment_magic_prefix(line) if not cell_magic else None
                # Keep assignment stores visible to flow analysis; never infer the RHS.
                masked = (
                    prefix + token.removeprefix("# ") + "  " + token
                    if prefix is not None
                    else indent + "pass  " + token
                )
                magics.append((masked, line))
                combined.append(masked + "\n")
            else:
                combined.append(line)
        if cell.source and not cell.source.endswith(("\n", "\r")):
            combined.append("\n")
    # A final marker makes even a CR-only cell an interior, lossless CST segment.
    combined.append(f"{marker}end__\n")
    return Notebook(
        source, cells, marker, "".join(combined), tuple(magics), tuple(environment)
    )


def _upgrade_magic(line: str, options: TransformOptions) -> tuple[str, bool]:
    if not re.match(r"\s*[!%]pip\s+install\b", line):
        return line, False
    names = "|".join(re.escape(name) for name in options.mapping.package_old)
    requirement = re.compile(
        r"(?<!\S)(?P<quote>['\"]?)(?P<name>"
        + names
        + r")(?P<extras>\[[^\]\s]*\])?(?P<spec>(?:[<>=!~][^\s'\"]*)?)(?P=quote)(?=\s|$)",
        re.IGNORECASE,
    )

    def replace_requirement(match: re.Match[str]) -> str:
        extras = [part for part in (match["extras"] or "[]")[1:-1].split(",") if part]
        extras.extend(extra for extra in options.mapping.extras if extra not in extras)
        target = (
            options.mapping.package_new
            + ("[" + ",".join(extras) + "]" if extras else "")
            + target_constraint(
                match["name"],
                match["spec"],
                options.mapping.package_new,
                options.mapping.specifier,
            )
        )
        return '"' + target + '"'

    rewritten, count = requirement.subn(replace_requirement, line)
    return (rewritten if options.mode == "upgrade" else line), count > 0


def transform_notebook(
    notebook: Notebook, options: TransformOptions, context: SourceContext
) -> SourceResult:
    """Transform one module, then restore code cells without touching outputs."""
    from .transform import transform_source

    result = transform_source(notebook.combined, options, context)
    if any(item["kind"] == "unsupported-file" for item in result.items):
        try:
            ast.parse(notebook.combined)
        except SyntaxError as error:
            # LibCST may point at the following synthetic marker; AST owns the
            # offending physical line and avoids exposing combined-source excerpts.
            result = replace(
                result,
                items=(
                    Item(
                        file=context.file,
                        line=error.lineno or 0,
                        column=error.offset or 0,
                        kind="unsupported-file",
                        message=(
                            f"Cannot parse Python source: {error.msg}"
                            if result.items[0]["message"].startswith(
                                "Cannot parse Python source:"
                            )
                            else result.items[0]["message"]
                        ),
                    ),
                ),
            )
    transformed = result.source
    magic_found = False
    for token, original in notebook.magics:
        replacement, found = _upgrade_magic(original, options)
        magic_found = magic_found or found
        transformed = transformed.replace(
            token + "\n",
            replacement if replacement.endswith(("\n", "\r")) else replacement + "\n",
        )
    spans = cell_source_spans(notebook.original)
    replacements: list[tuple[int, int, str]] = []
    for position, cell in enumerate(notebook.cells):
        start = transformed.index(f"{notebook.marker}{cell.index}__\n") + len(
            f"{notebook.marker}{cell.index}__\n"
        )
        end = (
            transformed.index(
                f"{notebook.marker}{notebook.cells[position + 1].index}__\n"
            )
            if position + 1 < len(notebook.cells)
            else transformed.index(f"{notebook.marker}end__\n")
        )
        code = transformed[start:end]
        if (
            cell.source
            and not cell.source.endswith(("\n", "\r"))
            and code.endswith("\n")
        ):
            code = code[:-1]
        if code != cell.source:
            span = spans[cell.index]
            if span is not None:
                replacements.append(
                    (
                        span.start,
                        span.end,
                        rewrite_source_value(notebook.original, span, code),
                    )
                )
    source = notebook.original
    for start, end, replacement in reversed(replacements):
        source = source[:start] + replacement + source[end:]
    locations: dict[int, tuple[int, int]] = {}
    row = 1
    for cell in notebook.cells:
        lines = source_lines(cell.source)
        locations[row] = (cell.index + 1, 1)
        for local_row, _ in enumerate(lines, 1):
            locations[row + local_row] = (cell.index + 1, local_row)
        row += len(lines) + 1
        # The older AST grammar may reject syntax at the final synthetic marker.
        locations[row] = (cell.index + 1, max(1, len(lines)))
    items: list[Item] = []
    for item in result.items:
        location = locations.get(item["line"])
        if location is None:
            items.append(item)
            continue
        cell_index, local_row = location
        items.append(
            {**item, "file": f"{item['file']}#cell={cell_index}", "line": local_row}
        )
    return SourceResult(source, tuple(items), result.found or magic_found, magic_found)
