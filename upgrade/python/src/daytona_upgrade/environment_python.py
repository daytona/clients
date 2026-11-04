# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""AST-only environment writes using the existing lexical binding analysis."""

from __future__ import annotations

import ast
from dataclasses import replace
from typing import TYPE_CHECKING

from .dotenv_environment import SETTINGS_CLASSES, DotenvLoads
from .environment import VARIABLES, EnvironmentWrite
from .environment_paths import EnvironmentResolver
from .flow import Flow, SourceContext
from .lexical import source_lines
from .models import Item, TransformOptions

if TYPE_CHECKING:
    from .notebooks import Notebook


class _EnvironmentFlow(Flow):
    """Accumulate writes while inherited flow tracks aliases and shadowing."""

    def __init__(self, options: TransformOptions, context: SourceContext) -> None:
        source = (
            (context.root / context.file).resolve()
            if context.root is not None
            else None
        )
        super().__init__(
            EnvironmentResolver(options.mapping, context.index, source), context
        )
        self.options = options
        self.items: list[Item] = []
        self.dotenv = DotenvLoads(options, context)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Environment preflight keeps possible loader provenance even if a
        # decorator replaces the class; this never certifies rewrite safety.
        settings = any(
            self.resolver.infer(base, self.env) in SETTINGS_CLASSES
            for base in node.bases
        )
        self.items.extend(self.dotenv.scan(node, self.resolver, self.env))
        super().visit_ClassDef(node)
        if settings:
            self.env[node.name] = "settings-class"

    def record(self, key: ast.expr, value: ast.AST | None) -> None:
        self.record_kind(key, self.resolver.infer(value, self.env) if value else "")

    def record_kind(self, key: ast.expr, resolved_value: str) -> None:
        """Keep RHS values captured before stores separate from live target keys."""
        resolved_key = self.resolver.infer(key, self.env)
        if resolved_key.startswith("literal:") and resolved_key[8:] not in VARIABLES:
            return
        if resolved_value == "literal:":
            return
        literal = (
            resolved_value[8:]
            if resolved_key[8:] in VARIABLES and resolved_value.startswith("literal:")
            else None
        )
        write = EnvironmentWrite(
            self.context.file,
            key.lineno,
            key.col_offset + 1,
            literal,
            resolved_key[8:] if resolved_key[8:] in VARIABLES else None,
        )
        self.items.extend(write.items(self.options.drop_api_url))

    def assignment(
        self, target: ast.AST, value: ast.expr | None
    ) -> list[tuple[ast.AST, str]]:
        """Capture unpacked RHS proofs before any left-to-right target stores."""
        if isinstance(target, (ast.Tuple, ast.List)):
            paired = (
                isinstance(value, (ast.Tuple, ast.List))
                and len(target.elts) == len(value.elts)
                and not any(
                    isinstance(element, ast.Starred)
                    for element in (*target.elts, *value.elts)
                )
            )
            return [
                binding
                for index, element in enumerate(target.elts)
                for binding in self.assignment(
                    element,
                    value.elts[index]
                    if paired and isinstance(value, (ast.Tuple, ast.List))
                    else None,
                )
            ]
        if isinstance(target, ast.Starred):
            return self.assignment(target.value, None)
        kind = (
            self.kinds.get(
                (
                    value.lineno,
                    value.col_offset,
                    value.end_lineno or value.lineno,
                    value.end_col_offset or value.col_offset,
                ),
                "",
            )
            if value
            else ""
        )
        return [(target, kind)]

    def store(self, assignments: list[tuple[ast.AST, str]]) -> None:
        """Evaluate each target with preceding stores already reflected in bindings."""
        for target, kind in assignments:
            self.visit(target)
            if (
                isinstance(target, ast.Subscript)
                and self.resolver.infer(target.value, self.env) == "module:os.environ"
            ):
                self.record_kind(target.slice, kind)
            self.bind(target, kind)

    def visit_For(self, node: ast.For | ast.AsyncFor) -> None:
        self.store(self.assignment(node.target, None))
        super().visit_For(node)

    visit_AsyncFor = visit_For

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        assignments = [
            binding
            for target in node.targets
            for binding in self.assignment(target, node.value)
        ]
        self.store(assignments)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            self.store(self.assignment(node.target, node.value))

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if (
            isinstance(node.target, ast.Subscript)
            and self.resolver.infer(node.target.value, self.env) == "module:os.environ"
        ):
            self.record(node.target.slice, None)
        environment_union = (
            isinstance(node.op, ast.BitOr)
            and self.resolver.infer(node.target, self.env) == "module:os.environ"
        )
        if environment_union:
            self.record_mapping(node.value)
        super().visit_AugAssign(node)
        if environment_union:
            # In-place union changes entries, not the environment mapping identity.
            self.bind(node.target, "module:os.environ")

    def visit_Call(self, node: ast.Call) -> None:
        self.items.extend(self.dotenv.scan(node, self.resolver, self.env))
        function = self.resolver.infer(node.func, self.env)
        if function == "module:os.environ.update":
            for argument in node.args:
                self.record_mapping(argument)
            for keyword in node.keywords:
                if keyword.arg is None:
                    self.record_mapping(keyword.value)
                else:
                    self.record(
                        ast.copy_location(ast.Constant(keyword.arg), keyword.value),
                        keyword.value,
                    )
        if function in {
            "module:os.putenv",
            "module:os.environ.setdefault",
            "module:os.environ.__setitem__",
        }:
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            key = node.args[0] if node.args else keywords.get("key")
            value_keyword = (
                "default" if function == "module:os.environ.setdefault" else "value"
            )
            value = node.args[1] if len(node.args) >= 2 else keywords.get(value_keyword)
            if key is not None:
                self.record(key, value)
        super().visit_Call(node)

    def record_mapping(self, node: ast.expr) -> None:
        """Inspect literal update mappings; leave unknown mappings for manual review."""
        if self.resolver.infer(node, self.env) == "dotenv-values":
            return  # Its loader already scanned the file or recorded dynamic-input review.
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if key is None:
                    self.record_mapping(value)
                else:
                    self.record(key, value)
        elif isinstance(node, (ast.List, ast.Tuple)):
            for pair in node.elts:
                if isinstance(pair, (ast.List, ast.Tuple)) and len(pair.elts) == 2:
                    self.record(pair.elts[0], pair.elts[1])
                else:
                    self.record_mapping(pair)
        else:
            write = EnvironmentWrite(
                self.context.file, node.lineno, node.col_offset + 1, None
            )
            self.items.extend(write.items(self.options.drop_api_url))


def scan_python_environment(
    source: str, options: TransformOptions, context: SourceContext
) -> tuple[Item, ...]:
    """Inspect original Python without changing imports, proofs, or transforms."""
    if (context.module, source) in context.index.skipped_sources:
        return ()
    flow = _EnvironmentFlow(options, context)
    try:
        flow.analyze(source)
    except SyntaxError:
        # The normal transform owns syntax diagnostics, including notebook magics.
        return ()
    return tuple(flow.items)


def scan_notebook_environment(
    notebook: Notebook, options: TransformOptions, context: SourceContext
) -> tuple[Item, ...]:
    """Scan masked code and use the adapter's cell-local report coordinates."""
    locations: dict[int, tuple[int, int]] = {}
    row = 1
    for cell in notebook.cells:
        lines = source_lines(cell.source)
        locations[row] = (cell.index + 1, 1)
        for local_row, _ in enumerate(lines, 1):
            locations[row + local_row] = (cell.index + 1, local_row)
        row += len(lines) + 1
    items: list[Item] = []
    magic_items = tuple(
        item
        for write in notebook.environment
        for item in replace(write, file=context.file).items(options.drop_api_url)
    )
    for item in (
        *magic_items,
        *scan_python_environment(notebook.combined, options, context),
    ):
        location = locations.get(item["line"]) if item["file"] == context.file else None
        if location is None:
            items.append(item)
        else:
            cell_index, local_row = location
            items.append(
                {**item, "file": f"{item['file']}#cell={cell_index}", "line": local_row}
            )
    return tuple(items)
