# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Locate constructor calls whose credential checks move outside 0.x handlers."""

from __future__ import annotations

import ast
import sys
from collections.abc import Mapping

import libcst as cst
from libcst.metadata import CodeRange, MetadataWrapper, PositionProvider

from .comments import leading_comments
from .constructor_access import ConstructorAccess
from .flow import Flow
from .models import Item
from .resolution import Resolver, dotted
from .shared_error_guidance import expression_proof
from .subclasses import client_constructor


def constructor_guidance(
    flow: Flow, rewritten: cst.Module, constructors: Mapping[tuple[int, int], cst.Call]
) -> tuple[cst.Module, tuple[Item, ...]]:
    """Diagnose actual rewritten accesses while reporting original source positions."""
    original = constructor_error_items(flow)
    if not original:
        return rewritten, ()
    updated_flow = Flow(
        Resolver(flow.resolver.mapping, flow.context.index), flow.context
    )
    updated_flow.analyze(rewritten.code)
    remaining = dict(constructor_error_items(updated_flow))
    wrapper = MetadataWrapper(rewritten, unsafe_skip_copy=True)
    positions = wrapper.resolve(PositionProvider)
    comments: dict[int, list[str]] = {}
    items: list[Item] = []
    for position, item in original:
        updated_call = constructors.get(position)
        if updated_call is None:
            continue
        start = positions[updated_call].start
        # Convert CST character columns back to the AST's UTF-8 byte columns.
        column = len(
            updated_flow.text.lines[start.line - 1][: start.column].encode("utf-8")
        )
        if current := remaining.get((start.line, column)):
            items.append(item)
            comments.setdefault(current["line"], []).append(
                "FIXME(daytona-upgrade): " + item["message"]
            )
    return wrapper.visit(ConstructorComments(updated_flow, comments)), tuple(items)


class ConstructorComments(cst.CSTTransformer):
    """Add only surviving diagnostics after all executable rewrites have finished."""

    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, flow: Flow, comments: dict[int, list[str]]) -> None:
        self.flow = flow
        self.comments = comments

    def on_leave(
        self, original_node: cst.CSTNode, updated_node: cst.CSTNode
    ) -> cst.CSTNode | cst.RemovalSentinel | cst.FlattenSentinel[cst.CSTNode]:
        if isinstance(
            updated_node,
            (cst.SimpleStatementLine, cst.BaseCompoundStatement, cst.MatchCase),
        ):
            position = self.get_metadata(PositionProvider, original_node)
            assert isinstance(position, CodeRange)
            line = position.start.line
            messages = self.comments.get(line, [])
            prefix = "\n".join(
                self.flow.text.lines[max(0, line - len(messages) - 3) : line - 1]
            )
            return updated_node.with_changes(
                leading_lines=leading_comments(
                    updated_node.leading_lines, messages, ("upgrade", prefix)
                )
            )
        return updated_node


def constructor_error_items(flow: Flow) -> tuple[tuple[tuple[int, int], Item], ...]:
    """Use evaluated constructor/error proofs, excluding deferred scope bodies."""
    mapping = flow.resolver.mapping
    try_star = getattr(ast, "TryStar", None)
    # The former root aliases (root_alias_categories) name shared categories too.
    shared = (set(mapping.root_names) - set(mapping.root_imports)) | set(
        mapping.root_alias_categories
    )
    errors = {
        f"module:{module}.{name}"
        for root in mapping.modules
        for module in (
            root,
            root + ".v0",
            root + ".common.errors",
            root + ".v0.common.errors",
        )
        for name in shared
    }
    parents = {
        child: parent
        for parent in ast.walk(flow.module)
        for child in ast.iter_child_nodes(parent)
    }

    def catches(handler: ast.ExceptHandler, env: dict[str, str]) -> bool:
        exception = handler.type
        if exception is None:
            return True
        types = exception.elts if isinstance(exception, ast.Tuple) else [exception]
        return any(
            flow.resolver.infer(value, env)
            in errors | {"module:builtins.Exception", "module:builtins.BaseException"}
            or isinstance(value, ast.Name)
            and value.id in {"Exception", "BaseException"}
            and value.id not in env
            and value.id not in flow.context.index.builtin_writes
            for value in types
        )

    items: list[tuple[tuple[int, int], Item]] = []
    accesses: ConstructorAccess | None = None
    for node in ast.walk(flow.module):
        # isinstance AST dispatch preserves the supported Python 3.9 runtime.
        if not isinstance(node, ast.Call) or not client_constructor(
            expression_proof(node.func, flow)
        ):
            continue
        child: ast.AST = node
        parent = parents.get(child)
        statement: ast.stmt | None = None
        receiver = ""
        protected = False
        env = flow.constructor_envs.get((node.lineno, node.col_offset), {})
        while parent is not None:
            if isinstance(parent, ast.NamedExpr) and parent.value is node:
                receiver = dotted(parent.target) or receiver
            if (
                isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child in parent.body
                or isinstance(parent, ast.Lambda)
                and child is parent.body
            ):
                break
            if (
                isinstance(parent, ast.GeneratorExp)
                and child is not parent.generators[0].iter
            ):
                break
            if statement is None and isinstance(parent, ast.stmt):
                statement = parent
                if isinstance(parent, ast.Assign) and parent.value is node:
                    receiver = dotted(parent.targets[0]) or receiver
                elif isinstance(parent, ast.AnnAssign) and parent.value is node:
                    receiver = dotted(parent.target) or receiver
            if (
                (
                    isinstance(parent, ast.Try)
                    or sys.version_info >= (3, 11)
                    and try_star is not None
                    and isinstance(parent, ast.TryStar)
                )
                and child in parent.body
                and any(catches(handler, env) for handler in parent.handlers)
            ):
                if accesses is None:
                    accesses = ConstructorAccess(flow)
                protected = not accesses.accessed(node, parent)
                break
            child, parent = parent, parents.get(parent)
        if protected and statement is not None:
            # Name the receiver only when the client is bound to one (TS parity).
            advice = (
                f"access {receiver}.v0 inside this try (for example, v0 = {receiver}.v0)"
                if receiver
                else "access .v0 on this client inside the try"
            )
            message = f"SDK 2.x builds the v0 client on first use, so credential errors no longer happen in this constructor; {advice} to keep the error handling."
            items.append(
                (
                    (node.lineno, node.col_offset),
                    Item(
                        file=flow.context.file,
                        line=statement.lineno,
                        column=statement.col_offset + 1,
                        kind="fixme",
                        message=message,
                    ),
                )
            )
    return tuple(items)
