# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Retain unresolved mock targets without seeding unrelated literal-only patches."""

from __future__ import annotations

import ast

from .resolution import dotted


def unresolved_mock_targets(tree: ast.Module) -> bool:
    """Object patches need SDK links; plain dynamic patch targets need review alone.

    Import neighbors still retain SDK-backed objects, aliases and re-exports.
    SDK literals, environment changes and builtin effects are seeded separately.
    Escaped helpers conservatively retain analysis instead of guessing their use.
    """
    helpers = {
        "patch",
        "unittest.mock.patch",
        "mock.patch",
        "mocker.patch",
        "monkeypatch.setattr",
    }
    roots = {"patch", "unittest", "mock", "mocker", "monkeypatch"}
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module in {"unittest.mock", "mock"}:
                for alias in node.names:
                    if alias.name == "patch":
                        helpers.add(alias.asname or alias.name)
                        roots.add(alias.asname or alias.name)
            elif node.module == "unittest":
                for alias in node.names:
                    if alias.name == "mock":
                        root = alias.asname or alias.name
                        roots.add(root)
                        helpers.add(root + ".patch")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "unittest":
                    root = alias.asname or alias.name
                    roots.add(root)
                    helpers.add(root + ".mock.patch")
                elif alias.name in {"unittest.mock", "mock"}:
                    root = alias.asname or alias.name
                    roots.add(root.split(".")[0])
                    helpers.add(root + ".patch")
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in roots
        ):
            expression: ast.AST = node
            parent = parents.get(expression)
            while isinstance(parent, ast.Attribute):
                expression, parent = parent, parents.get(parent)
            name = dotted(expression)
            helper_value = any(
                name == helper
                or name.startswith(helper + ".")
                or helper.startswith(name + ".")
                for helper in helpers
            )
            if helper_value and (
                not isinstance(parent, ast.Call) or parent.func is not expression
            ):
                return True
        if not isinstance(node, ast.Call):
            continue
        name = dotted(node.func)
        if name not in helpers:
            continue
        target = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "target"),
            node.args[0] if node.args else None,
        )
        if any(isinstance(argument, ast.Starred) for argument in node.args) or any(
            keyword.arg is None for keyword in node.keywords
        ):
            return True
        if not isinstance(target, ast.Constant) or not isinstance(target.value, str):
            return True
    return False
