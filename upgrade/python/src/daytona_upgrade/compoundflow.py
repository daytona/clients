# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Reaching-path joins for exception handlers and structural pattern arms."""

from __future__ import annotations

import ast
import sys
from typing import Protocol

from .annotation_exports import joined_bindings
from .loopflow import LoopVisitor


class TryBlock(Protocol):
    """Shared AST structure of try and Python 3.11's exception-group try."""

    body: list[ast.stmt]
    orelse: list[ast.stmt]
    handlers: list[ast.ExceptHandler]
    finalbody: list[ast.stmt]


class CompoundVisitor(LoopVisitor):
    """Preserve client uncertainty without admitting abrupt exits to normal joins."""

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        before = self.env.copy()
        after: dict[str, str] | None = None
        exhaustive = False
        for case in node.cases:
            self.env = before.copy()
            self.kill_stores(case.pattern)
            self.visit(case.pattern)
            # Python does not promise to undo captures from a failed pattern.
            before = joined_bindings(self.resolver, (before, self.env.copy()))
            if case.guard:
                self.visit(case.guard)
                # A false guard still evaluates its writes before the next arm.
                before = joined_bindings(self.resolver, (before, self.env.copy()))
            if self.visit_suite(case.body):
                after = joined_bindings(self.resolver, (after, self.env.copy()))
            if (
                isinstance(case.pattern, ast.MatchAs)
                and case.pattern.pattern is None
                and case.guard is None
            ):
                exhaustive = True
                break
        if not exhaustive:
            after = joined_bindings(self.resolver, (after, before))
        self.reaching[node] = after is not None
        self.env = after if after is not None else {}

    def visit_Try(self, node: TryBlock) -> None:
        exits = (
            [
                (paths, len(paths))
                for paths in (self.loops[-1].breaks, self.loops[-1].continues)
            ]
            if self.loops
            else []
        )
        prefixes = self.env.copy()
        final_entry = prefixes.copy()
        if node.finalbody:
            self.prefix_proofs = (*self.prefix_proofs, final_entry)
        self.prefix_proofs = (*self.prefix_proofs, prefixes)
        reaches = self.visit_suite(node.body)
        self.prefix_proofs = self.prefix_proofs[:-1]
        reaches = reaches and self.visit_suite(node.orelse)
        after = self.env.copy() if reaches else None
        grouped = sys.version_info >= (3, 11) and isinstance(node, ast.TryStar)
        for handler in node.handlers:
            self.env = prefixes.copy()
            if grouped:
                # A raising branch still reaches later matching subgroups. Observe
                # every prefix before normal-only joins discard its bindings.
                self.prefix_proofs = (*self.prefix_proofs, prefixes)
            self.visit(handler)
            if grouped:
                self.prefix_proofs = self.prefix_proofs[:-1]
            if self.reaching[handler]:
                after = joined_bindings(self.resolver, (after, self.env.copy()))
        if node.finalbody:
            self.prefix_proofs = self.prefix_proofs[:-1]
        self.env = after if after is not None else {}
        if node.finalbody:
            # Finally also runs while an exception/return is unwinding. Calls in
            # it must not inherit only the normally completed try-body proof.
            self.env = final_entry
            # Preserve the existing annotation fence for wildcard imports even
            # when an exceptional prefix cannot establish their export surface.
            if any(
                isinstance(child, ast.ImportFrom)
                and any(alias.name == "*" for alias in child.names)
                for statement in [*node.body, *node.orelse, *node.handlers]
                for child in ast.walk(statement)
            ):
                for name in tuple(self.type_bindings):
                    self.fence(name)
            pending = [(paths, start, len(paths)) for paths, start in exits]
            reaches = self.visit_suite(node.finalbody)
            for paths, start, end in pending:
                paths[start:end] = (
                    [
                        joined_bindings(self.resolver, (branch, self.env))
                        for branch in paths[start:end]
                    ]
                    if reaches
                    else []
                )
            if not reaches:
                after = None
        if isinstance(node, ast.AST):
            self.reaching[node] = after is not None

    visit_TryStar = visit_Try
