# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Loop joins distinguish exhaustion, break, and iteration backedges."""

from __future__ import annotations

import ast
from typing import NamedTuple

from .annotation_exports import joined_bindings
from .collection_proofs import iterable_element
from .controlflow import BindingVisitor
from .proof_mutations import invalidate
from .resolution import dotted


class LoopPaths(NamedTuple):
    """Accumulate exit snapshots for exactly one lexically enclosing loop."""

    breaks: list[dict[str, str]]
    continues: list[dict[str, str]]


class LoopVisitor(BindingVisitor):
    """Carry uncertain client evidence across zero or multiple iterations."""

    binding: str = ""
    loops: tuple[LoopPaths, ...] = ()
    previewing_loop: bool = False

    def loop_probe(self) -> LoopVisitor | None:
        """Call-site analyses opt in; class-wide write collectors need no replay."""
        return None

    def visit_Break(self, node: ast.Break) -> None:
        if self.loops:
            self.loops[-1].breaks.append(self.env.copy())

    def visit_Continue(self, node: ast.Continue) -> None:
        if self.loops:
            self.loops[-1].continues.append(self.env.copy())

    def loop_entry(self, node: ast.For | ast.AsyncFor | ast.While) -> None:
        """A later iteration may observe any body write, never an old proof."""
        before = self.env.copy()
        assigned = self.assigned_names(node)
        for name in assigned:
            if name == "*":
                self.env.clear()
                self.type_bindings.clear()
            else:
                invalidate(self.env, name)
        # Parent/container writes can invalidate descendants or aliased members.
        affected = assigned | {
            name for name in before if self.env.get(name) != before[name]
        }
        self.env = joined_bindings(self.resolver, (before, self.env))
        if self.previewing_loop:
            return
        stable = {name: kind for name, kind in self.env.items() if name not in affected}
        # Join to a fixed point: each round can carry evidence one alias further.
        # Preview visitors never recursively replay nested loops.
        for _ in range(len(affected) + 1):
            probe = self.loop_probe()
            if probe is None:
                return
            probe.previewing_loop = True
            probe.prefix_proofs = ()
            paths = LoopPaths([], [])
            probe.loops = (paths,)
            if isinstance(node, (ast.For, ast.AsyncFor)):
                probe.bind(
                    node.target,
                    iterable_element(self.resolver.infer(node.iter, before)),
                )
            reaches = probe.visit_suite(node.body)
            previous = self.env.copy()
            for branch in [*paths.continues, *([probe.env] if reaches else [])]:
                # A speculative abrupt nested path may drop its whole environment;
                # that says nothing about names this loop never assigns.
                affected |= {
                    name for name in branch if name.startswith("@member-write.")
                }
                carried = {
                    name: self.env[name] for name in affected if name in self.env
                }
                projected = {name: branch.get(name, "") for name in affected}
                self.env = {
                    **{
                        name: kind
                        for name, kind in stable.items()
                        if name not in affected
                    },
                    **joined_bindings(self.resolver, (carried, projected)),
                }
            if self.env == previous:
                break

    def loop_body(
        self, node: ast.For | ast.AsyncFor | ast.While, skipped: dict[str, str] | None
    ) -> None:
        paths = LoopPaths([], [])
        self.loops = (*self.loops, paths)
        reaches = self.visit_suite(node.body)
        self.loops = self.loops[:-1]
        exhaustion = skipped
        # A literal infinite loop can leave only through its own break paths.
        infinite = (
            isinstance(node, ast.While)
            and isinstance(node.test, ast.Constant)
            and node.test.value is True
        )
        if not infinite:
            for branch in [*paths.continues, *([self.env] if reaches else [])]:
                exhaustion = joined_bindings(self.resolver, (exhaustion, branch))
        self.env = exhaustion if exhaustion is not None else {}
        else_reaches = exhaustion is not None and self.visit_suite(node.orelse)
        after = self.env.copy() if else_reaches else None
        for branch in paths.breaks:
            after = joined_bindings(self.resolver, (after, branch))
        self.reaching[node] = after is not None
        self.env = after if after is not None else {}

    def visit_While(self, node: ast.While) -> None:
        before = self.env.copy()
        self.loop_entry(node)
        self.visit(node.test)
        if isinstance(node.test, ast.Constant) and node.test.value is False:
            self.env = before
            self.reaching[node] = self.visit_suite(node.orelse)
            return
        infinite = isinstance(node.test, ast.Constant) and node.test.value is True
        self.loop_body(node, None if infinite else self.env.copy())

    def visit_For(self, node: ast.For | ast.AsyncFor) -> None:
        previous = self.binding
        self.binding = dotted(node.target)
        self.visit(node.iter)
        self.binding = previous
        kind = self.resolver.infer(node.iter, self.env)
        before = self.env.copy()
        self.loop_entry(node)
        self.visit(node.target)
        self.bind(node.target, iterable_element(kind))
        self.loop_body(node, before)

    visit_AsyncFor = visit_For
