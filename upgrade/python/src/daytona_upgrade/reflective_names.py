# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Syntactic alias candidates fence writes; only the resolver can prove a call."""

from __future__ import annotations

import ast
from typing import Final

from .resolution import dotted

NAMESPACE_WRITERS: Final = frozenset(
    {"exec", "eval", "globals", "locals", "vars", "setattr", "__setattr__"}
)


def reflective_names(module: ast.AST) -> dict[str, str]:
    """Retain possible reflective aliases even after a conditional/rebinding kill."""
    aliases = {"builtins": "builtins", "setattr": "setattr", "vars": "vars"}
    assignments: list[ast.Assign] = []
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "builtins":
                    aliases[alias.asname or alias.name] = "builtins"
        elif isinstance(node, ast.ImportFrom) and node.module == "builtins":
            for alias in node.names:
                if alias.name in NAMESPACE_WRITERS | {"object", "getattr"}:
                    aliases[alias.asname or alias.name] = "builtins." + alias.name
        elif isinstance(node, ast.Assign):
            assignments.append(node)
    for _ in range(len(assignments) + 1):
        before = aliases.copy()
        for assignment in assignments:
            value = canonical_name(assignment.value, aliases)
            for target in assignment.targets:
                if isinstance(target, ast.Name) and value in {
                    "setattr",
                    "builtins.setattr",
                    "vars",
                    "builtins.vars",
                    "builtins",
                    "object",
                    "builtins.object",
                }:
                    aliases[target.id] = value
                if isinstance(target, ast.Name):
                    # Candidates only fence possible owners; they never prove identity.
                    pending = [assignment.value]
                    while pending:
                        candidate = pending.pop()
                        if isinstance(candidate, ast.Name):
                            owners = {
                                candidate.id,
                                *(
                                    owner
                                    for key, owner in before.items()
                                    if key.startswith("@owner:" + candidate.id + ":")
                                ),
                            }
                            for owner in owners:
                                aliases["@owner:" + target.id + ":" + owner] = owner
                        elif isinstance(candidate, ast.IfExp):
                            pending.extend((candidate.body, candidate.orelse))
                        elif isinstance(candidate, ast.BoolOp):
                            pending.extend(candidate.values)
        if aliases == before:
            break
    candidates = [
        node
        for node in ast.walk(module)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
        and node.value is not None
    ]
    for _ in range(len(candidates) + 1):
        before = aliases.copy()
        for node in candidates:
            if node.value is None or not namespace_writer(node.value, aliases):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name):
                        aliases["@namespace:" + name.id] = "writer"
        if aliases == before:
            break
    return aliases


def namespace_writer(node: ast.AST, aliases: dict[str, str]) -> bool:
    """Possible namespace access is a fence, including conditional/unpacked aliases."""
    name = canonical_name(node, aliases)
    if name in NAMESPACE_WRITERS or name.removeprefix("builtins.") in NAMESPACE_WRITERS:
        return True
    if isinstance(node, ast.Name):
        return "@namespace:" + node.id in aliases
    if isinstance(node, ast.Call):
        return (
            canonical_name(node.func, aliases) in {"getattr", "builtins.getattr"}
            and len(node.args) >= 2
            and canonical_name(node.args[0], aliases) == "builtins"
            and (
                not isinstance(node.args[1], ast.Constant)
                or node.args[1].value in NAMESPACE_WRITERS
            )
        )
    if isinstance(node, (ast.IfExp, ast.BoolOp, ast.Tuple, ast.List, ast.Starred)):
        return any(
            namespace_writer(child, aliases) for child in ast.iter_child_nodes(node)
        )
    return False


def canonical_name(node: ast.AST, aliases: dict[str, str]) -> str:
    """Resolve a candidate builtin spelling without certifying its runtime target."""
    name = dotted(node)
    root, separator, tail = name.partition(".")
    return aliases.get(root, root) + (separator + tail if separator else "")
