# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Binding-aware migration of string forward references, never runtime strings."""

from __future__ import annotations

import ast
import re
from typing import Protocol

import libcst as cst

from .annotation_flow import annotation_type_nodes
from .flow import Flow
from .models import ItemKind, Mapping, TransformOptions
from .resolution import Resolver
from .source_text import SourceText


class AnnotationContext(Protocol):
    flow: Flow
    options: TransformOptions
    newline: str

    def position(self, node: cst.CSTNode) -> tuple[int, int]: ...
    def item(self, node: cst.CSTNode, kind: ItemKind, message: str) -> None: ...
    def fixme(self, node: cst.CSTNode, message: str) -> None: ...


def rewrite_string_annotation(
    context: AnnotationContext, original: cst.SimpleString, updated: cst.SimpleString
) -> cst.BaseExpression:
    """Rewrite only binding-proven type references, preserving annotation layout."""
    env = context.flow.string_annotation_envs.get(context.position(original))
    value = original.evaluated_value
    if context.options.mode != "upgrade" or env is None or not isinstance(value, str):
        return updated
    try:
        replacement = rewrite_type_text(value, context.flow.resolver, env)
    except SyntaxError:
        # Invalid syntax cannot be rewritten; spelling alone is never SDK proof.
        if any(
            moved_reference(match[0], env, context.options.mapping)
            for match in re.finditer(r"\b\w+\.\w+", value)
        ):
            message = "Review this string annotation manually; moved SDK types must use daytona.v0."
            context.item(original, "fixme", message)
            context.fixme(original, message)
        return updated
    if replacement == value:
        return updated
    return updated.with_changes(
        value=quoted_text(original, replacement).replace("\n", context.newline)
    )


def quoted_text(literal: cst.SimpleString, value: str) -> str:
    """Encode decoded annotation text with its original quote style."""
    quote = literal.quote
    escaped = value.replace("\\", "\\\\").replace(quote[0], "\\" + quote[0])
    # Source newlines normalize a literal CR to LF, so CR is always escaped.
    escaped = escaped.replace("\r", "\\r")
    if len(quote) == 1:
        escaped = escaped.replace("\n", "\\n")
    # Raw strings cannot encode escaped delimiters without changing their value.
    prefix = literal.prefix.replace("r", "").replace("R", "")
    return prefix + quote + escaped + quote


def moved_reference(name: str, env: dict[str, str], mapping: Mapping) -> str:
    """Bare imports migrate at their declaration; only root-module paths move here."""
    owner, _, suffix = name.partition(".")
    if (
        env.get(owner) in {"module:daytona", "module:daytona_sdk"}
        and suffix
        and suffix.split(".")[0] not in (*mapping.root_imports, "v0", "v2")
    ):
        return ("daytona" if owner == "daytona_sdk" else owner) + ".v0." + suffix
    return ""


def rewrite_type_text(value: str, resolver: Resolver, env: dict[str, str]) -> str:
    """Apply AST byte-range edits only to types, excluding Literal/Annotated values."""
    parsed = ast.parse(value, mode="eval").body
    text = SourceText.parse(value)
    edits: list[tuple[int, int, bytes]] = []
    for node in annotation_type_nodes(parsed, lambda node: resolver.infer(node, env)):
        replacement = ""
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            replacement = moved_reference(
                node.value.id + "." + node.attr, env, resolver.mapping
            )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            inner = rewrite_type_text(node.value, resolver, env)
            if inner != node.value:
                literal = cst.parse_expression(text.segment(node) or "")
                if isinstance(literal, cst.SimpleString):
                    replacement = quoted_text(literal, inner)
        if (
            replacement
            and isinstance(node, ast.expr)
            and node.end_lineno is not None
            and node.end_col_offset is not None
        ):
            start = text.offsets[node.lineno - 1] + node.col_offset
            end = text.offsets[node.end_lineno - 1] + node.end_col_offset
            edits.append((start, end, replacement.encode("utf-8")))
    result = text.data
    for start, end, replacement_bytes in sorted(edits, reverse=True):
        result = result[:start] + replacement_bytes + result[end:]
    return result.decode("utf-8")
