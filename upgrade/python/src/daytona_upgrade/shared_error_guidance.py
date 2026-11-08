# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Locate v0 imports whose shared errors need root-import migration guidance."""

from __future__ import annotations

import ast

from ._workflow_scopes import WorkflowScopes
from .flow import Flow
from .resolution import dotted


def shared_error_imports(
    flow: Flow, scopes: WorkflowScopes
) -> tuple[tuple[ast.Import | ast.ImportFrom, frozenset[str]], ...]:
    """Each v0 import that brings shared errors, with the error names it brings.

    Reuse position-specific proofs to exclude shadowed module references. The
    former root aliases count: they name shared categories too.
    """
    mapping = flow.resolver.mapping
    shared_errors = (set(mapping.root_names) - set(mapping.root_imports)) | set(
        mapping.root_alias_categories
    )
    names: dict[ast.Import | ast.ImportFrom, set[str]] = {}
    modules = {
        old + ".v0"
        for old in mapping.modules
        if not flow.context.index.owns(old + ".v0")
    }

    def is_v0_module(module: str) -> bool:
        return not flow.context.index.owns(module) and any(
            module == root or module.startswith(root + ".") for root in modules
        )

    selected: set[ast.Import | ast.ImportFrom] = set()
    imports: dict[tuple[ast.AST, str, str], list[ast.Import]] = {}
    for node in scopes.nodes:
        # AST dispatch must remain compatible with the Python 3.9 runtime.
        if isinstance(node, ast.ImportFrom):
            if (
                not node.level
                and is_v0_module(node.module or "")
                and any(
                    alias.name in shared_errors or alias.name == "*"
                    for alias in node.names
                )
            ):
                selected.add(node)
                names.setdefault(node, set()).update(
                    alias.name for alias in node.names if alias.name in shared_errors
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if not is_v0_module(alias.name) and alias.name + ".v0" not in modules:
                    continue
                binding = alias.asname or alias.name
                owner = scopes.owner(binding.split(".")[0], node)
                imports.setdefault((owner, binding, alias.name), []).append(node)
    for node in scopes.nodes:
        if not (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Load)
            and node.attr in shared_errors
        ):
            continue
        value = node.value
        module = expression_proof(value, flow).removeprefix("module:")
        if (
            not is_v0_module(module)
            or expression_proof(node, flow) != f"module:{module}.{node.attr}"
        ):
            continue
        binding = dotted(value)
        owner = scopes.owner(binding.split(".")[0], value)
        candidates = imports.get((owner, binding, module), [])
        if binding.endswith(".v0"):
            candidates = [
                *candidates,
                *imports.get(
                    (owner, binding.removesuffix(".v0"), module.removesuffix(".v0")), []
                ),
            ]
        preceding = [
            candidate
            for candidate in candidates
            if (candidate.lineno, candidate.col_offset) < (node.lineno, node.col_offset)
        ]
        if preceding:
            nearest = max(preceding, key=lambda node: (node.lineno, node.col_offset))
            selected.add(nearest)
            names.setdefault(nearest, set()).add(node.attr)
    return tuple(
        (node, frozenset(names.get(node, ())))
        for node in scopes.nodes
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node in selected
    )


def expression_proof(node: ast.expr, flow: Flow) -> str:
    """Read an expression's proof at evaluation time, not from the final env."""
    return flow.kinds.get(
        (
            node.lineno,
            node.col_offset,
            node.end_lineno or node.lineno,
            node.end_col_offset or node.col_offset,
        ),
        "",
    )
