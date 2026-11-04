# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Formatting-preserving import and test-double target migration."""

from __future__ import annotations

import ast

import libcst as cst
from libcst.helpers import get_full_name_for_node

from .models import Mapping
from .resolution import ProjectIndex, Resolver
from .subclasses import client_constructor, member_receiver


def module_name(node: cst.CSTNode | None) -> str:
    return (get_full_name_for_node(node) or "") if node is not None else ""


def moved_module(name: str, mapping: Mapping, index: ProjectIndex | None = None) -> str:
    if index is not None and index.owns(name):
        return name
    for old in mapping.modules:
        if name == old:
            return "daytona"
        if name.startswith(old + "."):
            suffix = name[len(old) + 1 :]
            return (
                "daytona." + suffix
                if suffix.split(".", 1)[0] in ("v0", "v2")
                else "daytona.v0." + suffix
            )
    return name


def split_import(
    node: cst.ImportFrom, mapping: Mapping, index: ProjectIndex
) -> list[cst.ImportFrom]:
    if node.relative or index.owns(module_name(node.module)):
        return [node]
    name = module_name(node.module)
    if isinstance(node.names, cst.ImportStar):
        # The bundled v0 __all__ excludes client/config constructors, so it
        # cannot shadow the root Daytona. Keep shared errors at their v0 types.
        modules = (
            ("daytona", "daytona.v0")
            if name in mapping.modules
            else (moved_module(name, mapping),)
        )
        return [
            node.with_changes(module=cst.parse_expression(module)) for module in modules
        ]
    if name not in mapping.modules:
        return [
            node.with_changes(module=cst.parse_expression(moved_module(name, mapping)))
        ]
    root = [
        alias
        for alias in node.names
        if module_name(alias.name) in (*mapping.root_imports, "v0", "v2")
        or module_name(alias.name).startswith("__")
        and module_name(alias.name).endswith("__")
    ]
    resource = [alias for alias in node.names if alias not in root]
    result: list[cst.ImportFrom] = []
    for module, aliases in [("daytona", root), ("daytona.v0", resource)]:
        if aliases:
            result.append(
                import_layout(
                    node.with_changes(module=cst.parse_expression(module)), aliases
                )
            )
    return result


def import_layout(
    node: cst.ImportFrom, aliases: list[cst.ImportAlias]
) -> cst.ImportFrom:
    """Normalize split separators without transferring a removed name's indentation."""
    if not node.lpar or "\n" not in cst.Module([]).code_for_node(node):
        return node.with_changes(
            names=[
                *aliases[:-1],
                aliases[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT),
            ]
        )
    initial = node.lpar.whitespace_after
    continuation = (
        initial
        if isinstance(initial, cst.ParenthesizedWhitespace)
        else cst.ParenthesizedWhitespace(
            first_line=cst.TrailingWhitespace(), last_line=cst.SimpleWhitespace("    ")
        )
    )
    if not isinstance(node.names, cst.ImportStar) and aliases[0] is not node.names[0]:
        continuation = continuation.with_changes(
            first_line=cst.TrailingWhitespace(newline=continuation.first_line.newline),
            empty_lines=(),
        )
    names: list[cst.ImportAlias] = []
    for index, alias in enumerate(aliases):
        comma = alias.comma if isinstance(alias.comma, cst.Comma) else cst.Comma()
        whitespace = comma.whitespace_after
        if not isinstance(alias.comma, cst.Comma) and node.rpar:
            whitespace = node.rpar.whitespace_before
        if not isinstance(whitespace, cst.ParenthesizedWhitespace):
            whitespace = cst.ParenthesizedWhitespace(
                first_line=cst.TrailingWhitespace()
            )
        separator = whitespace.with_changes(
            indent=True,
            last_line=continuation.last_line
            if index < len(aliases) - 1
            else cst.SimpleWhitespace(""),
        )
        names.append(
            alias.with_changes(comma=comma.with_changes(whitespace_after=separator))
        )
    return node.with_changes(
        names=names,
        lpar=node.lpar.with_changes(whitespace_after=continuation),
        rpar=cst.RightParen(),
    )


def mock_target_kind(value: str, mapping: Mapping, index: ProjectIndex) -> str:
    """Resolve a runtime patch path without trusting its terminal spelling."""
    parts = value.split(".")
    if not all(part.isidentifier() for part in parts):
        return ""
    node: ast.expr = ast.Name(id=parts[0], ctx=ast.Load())
    for part in parts[1:]:
        node = ast.Attribute(value=node, attr=part, ctx=ast.Load())
    namespace = "local-module:" if index.owns(parts[0]) else "module:"
    return Resolver(mapping, index).infer(node, {parts[0]: namespace + parts[0]})


def mock_target(
    value: str, mapping: Mapping, index: ProjectIndex | None = None
) -> tuple[str, bool]:
    prefix, _, member = value.rpartition(".")
    kind = ""
    constructor = index is not None and client_constructor(
        mock_target_kind(value, mapping, index)
    )
    if index is not None and member in mapping.moved and prefix:
        kind = member_receiver(mock_target_kind(prefix, mapping, index), member)
        if kind == "client":
            return prefix + ".v0." + member, False
        if prefix.endswith((".v0", ".v2")):
            return value, False
    manual = (
        constructor
        or member in mapping.moved
        and (
            client_constructor(kind)
            or kind.startswith("module:daytona")
            and (index is None or not index.owns(kind.removeprefix("module:")))
        )
    )
    if index is not None and index.owns(value):
        return value, manual
    manual = manual or member in mapping.moved and "daytona" in prefix.lower()
    for old in mapping.modules:
        if value.startswith(old + "."):
            suffix = value[len(old) + 1 :]
            first = suffix.split(".")[0]
            if first in ("Daytona", "AsyncDaytona"):
                parts = suffix.split(".")
                return "daytona." + suffix, len(parts) == 1 or parts[1] in mapping.moved
            if first in (*mapping.root_imports, "v0", "v2"):
                return "daytona." + suffix, False
            return "daytona.v0." + suffix, False
    return value, manual
