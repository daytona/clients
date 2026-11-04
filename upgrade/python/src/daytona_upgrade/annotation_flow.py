# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Snapshot annotation provenance before runtime flow binds or shadows names."""

from __future__ import annotations

import ast
import sys
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .flow import Flow


def record_annotations(flow: Flow, node: ast.AST) -> None:
    """Record config annotations and string forward-reference environments."""
    if isinstance(node, ast.Module):
        from .import_only_clients import import_only_clients

        flow.env.update(import_only_clients(node, flow.resolver))
    candidates = (
        [node]
        if isinstance(node, (ast.AnnAssign, ast.arg))
        else [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
            *(arg for arg in (node.args.vararg, node.args.kwarg) if arg is not None),
        ]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        else []
    )
    for candidate in candidates:
        record_string_annotation(flow, candidate.annotation)
        if (
            flow.resolver.annotation(candidate.annotation, flow.annotation_env())
            == "config"
        ):
            flow.config_annotations.add((candidate.lineno, candidate.col_offset))
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        record_string_annotation(flow, node.returns)
    if (
        isinstance(node, ast.AnnAssign)
        and node.value is not None
        and flow.resolver.infer(node.annotation, flow.annotation_env())
        in {"module:typing.TypeAlias", "module:typing_extensions.TypeAlias"}
    ):
        record_string_annotation(flow, node.value)
    if sys.version_info >= (3, 12):
        if isinstance(node, ast.TypeAlias):
            record_string_annotation(flow, node.value)
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.TypeAlias)
        ):
            for parameter in node.type_params:
                if isinstance(parameter, ast.TypeVar):
                    record_string_annotation(flow, parameter.bound)
                if sys.version_info >= (3, 13):
                    record_string_annotation(flow, parameter.default_value)
    if not isinstance(node, ast.Call):
        return
    # These runtime APIs accept types as arguments, including quoted types.
    kind = flow.resolver.infer(node.func, flow.env)
    if kind in {
        "module:typing.cast",
        "module:typing_extensions.cast",
        "module:typing.ForwardRef",
        "module:typing_extensions.ForwardRef",
    }:
        for argument in node.args[:1]:
            if not isinstance(argument, ast.Starred):
                record_string_annotation(flow, argument)
        for keyword in node.keywords:
            if keyword.arg == "typ" and kind.endswith(".cast"):
                record_string_annotation(flow, keyword.value)
    if kind in {
        "module:typing.TypeVar",
        "module:typing_extensions.TypeVar",
        "module:typing.ParamSpec",
        "module:typing_extensions.ParamSpec",
    }:
        for argument in node.args[1:] if kind.endswith(".TypeVar") else []:
            if not isinstance(argument, ast.Starred):
                record_string_annotation(flow, argument)
        for keyword in node.keywords:
            if keyword.arg in {"bound", "default"}:
                record_string_annotation(flow, keyword.value)
    if kind in {"module:typing.NewType", "module:typing_extensions.NewType"}:
        for argument in node.args[1:2]:
            if not isinstance(argument, ast.Starred):
                record_string_annotation(flow, argument)
        for keyword in node.keywords:
            if keyword.arg == "tp":
                record_string_annotation(flow, keyword.value)


def record_string_annotation(flow: Flow, annotation: ast.AST | None) -> None:
    """Traverse type syntax, including starred annotations, not runtime unpacks."""
    if annotation is None:
        return
    env = flow.annotation_env()
    for node in annotation_type_nodes(
        annotation, lambda value: flow.resolver.infer(value, env)
    ):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            flow.string_annotation_envs[node.lineno, node.col_offset] = env


def annotation_type_nodes(
    annotation: ast.AST, infer: Callable[[ast.AST], str]
) -> Iterator[ast.AST]:
    """Walk type syntax, never Literal payloads or Annotated's non-type extras."""
    if isinstance(annotation, ast.Subscript):
        kind = infer(annotation.value)
        if kind in {"module:typing.Literal", "module:typing_extensions.Literal"}:
            return
        if kind in {
            "module:typing.Annotated",
            "module:typing_extensions.Annotated",
        } and isinstance(annotation.slice, ast.Tuple):
            yield from annotation_type_nodes(annotation.slice.elts[0], infer)
            return
    yield annotation
    # Only ForwardRef's first argument is type syntax inside a call.
    # Lists occur in Callable[[...], ...]; starred operands unpack types.
    children = (
        [annotation.value, annotation.slice]
        if isinstance(annotation, ast.Subscript)
        else [annotation.value]
        if isinstance(annotation, ast.Starred)
        else [arg for arg in annotation.args[:1] if not isinstance(arg, ast.Starred)]
        if isinstance(annotation, ast.Call)
        and infer(annotation.func)
        in {"module:typing.ForwardRef", "module:typing_extensions.ForwardRef"}
        else [annotation.left, annotation.right]
        if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr)
        else annotation.elts
        if isinstance(annotation, (ast.Tuple, ast.List))
        else []
    )
    for child in children:
        yield from annotation_type_nodes(child, infer)
