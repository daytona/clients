# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Follow a returned parameter only to observed namespace reads or exact aliases."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, NamedTuple

from .collection_proofs import tuple_members
from .local_double_targets import DoubleTargets

if TYPE_CHECKING:
    from .flow import Flow


class RelayUse(NamedTuple):
    changed: bool = False
    safe: bool = True
    returned: str = ""
    namespace: bool = False


@dataclass(frozen=True)
class RelayUses:
    flow: Flow
    parents: dict[ast.AST, ast.AST]
    result: str
    conditions: tuple[tuple[str, bool], ...] = ()
    relays: dict[ast.expr, tuple[ast.Call, str]] = field(default_factory=dict)

    def inspect(
        self, value: ast.expr, path: str, seen: frozenset[ast.AST] = frozenset()
    ) -> RelayUse:
        if value in seen:
            return RelayUse(changed=True, safe=False)
        if self.excluded(value):
            return RelayUse()
        seen = seen | {value}
        parent = self.parents.get(value)
        if path == "?":
            if DoubleTargets(self.flow).kind(value) == "items:client":
                return RelayUse(
                    changed=not isinstance(parent, ast.Expr),
                    safe=False,
                    returned="?" if isinstance(parent, ast.Return) else "",
                )
            paths = (
                ["return"]
                if DoubleTargets(self.flow).kind(value) == "client"
                else [
                    str(index)
                    for index, kind in enumerate(
                        tuple_members(DoubleTargets(self.flow).kind(value))
                    )
                    if kind == "client"
                ]
            )
            return RelayUse(
                changed=any(
                    self.inspect(value, candidate, seen - {value}).changed
                    for candidate in paths
                ),
                safe=False,
                returned="?" if isinstance(parent, ast.Return) else "",
            )
        if path != "return":
            slot, *guards = path.split(":")
            if (
                isinstance(parent, ast.Subscript)
                and parent.value is value
                and isinstance(parent.slice, ast.Constant)
                and parent.slice.value == int(slot)
            ):
                return self.inspect(parent, "return", seen)
            if (
                isinstance(parent, ast.Assign)
                and len(parent.targets) == 1
                and isinstance(parent.targets[0], (ast.Tuple, ast.List))
            ):
                targets = parent.targets[0].elts
                if not any(
                    isinstance(target, ast.Starred) for target in targets
                ) and int(slot) < len(targets):
                    conditions = tuple(
                        (target.id, flag == "1")
                        for guard in guards
                        for index, _, flag in [guard.partition("=")]
                        if int(index) < len(targets)
                        and isinstance(target := targets[int(index)], ast.Name)
                        and self.stable(target)
                    )
                    return replace(self, conditions=self.conditions + conditions).alias(
                        targets[int(slot)], seen
                    )
            return RelayUse(changed=True, safe=False)
        if isinstance(parent, ast.Await) and parent.value is value:
            return self.inspect(parent, path, seen)
        if (
            isinstance(parent, ast.IfExp)
            and parent.test is not value
            or isinstance(parent, ast.BoolOp)
            and (isinstance(parent.op, ast.Or) or parent.values[-1] is value)
        ):
            return self.inspect(parent, path, seen)
        if value in self.relays:
            call, result_path = self.relays[value]
            return self.inspect(call, result_path, seen)
        if value in self.flow.client_arguments:
            return RelayUse(
                changed=True,
                safe=value in self.flow.adaptable_arguments,
                namespace=True,
            )
        if isinstance(parent, ast.Attribute) and parent.value is value:
            changed = (
                DoubleTargets(self.flow).kind(value) == "client"
                and (
                    isinstance(parent.ctx, ast.Load)
                    or parent.attr == "default_language"
                )
                and parent.attr in {*self.flow.resolver.mapping.moved, "v0"}
            )
            return RelayUse(changed=changed, safe=changed, namespace=changed)
        if (
            isinstance(parent, ast.Call)
            and parent.args
            and parent.args[0] is value
            and DoubleTargets(self.flow).kind(value) == "client"
            and DoubleTargets(self.flow).kind(parent.func)
            in {"call:builtins.getattr", "call:builtins.hasattr"}
            and DoubleTargets(self.flow)
            .kind(parent.func)
            .removeprefix("call:builtins.")
            not in self.flow.context.index.builtin_writes
            and len(parent.args)
            in (
                {2, 3}
                if DoubleTargets(self.flow).kind(parent.func).endswith("getattr")
                else {2}
            )
            and not parent.keywords
            and isinstance(parent.args[1], ast.Constant)
            and parent.args[1].value in self.flow.resolver.mapping.moved
        ):
            return RelayUse(changed=True, safe=False, namespace=True)
        if (
            isinstance(parent, ast.Assign)
            and parent.value is value
            and len(parent.targets) == 1
        ):
            return self.alias(parent.targets[0], seen)
        if isinstance(parent, ast.AnnAssign) and parent.value is value:
            return self.alias(parent.target, seen)
        if (
            isinstance(parent, ast.Return)
            and parent.value is value
            and self.result == "client"
        ):
            return RelayUse(returned="return")
        if isinstance(parent, ast.Tuple) and isinstance(
            self.parents.get(parent), ast.Return
        ):
            members = tuple_members(self.result)
            index = parent.elts.index(value)
            if len(members) == len(parent.elts) and members[index] in {
                "client",
                "unproven:client",
            }:
                return RelayUse(returned=str(index))
        if isinstance(parent, ast.Expr):
            return RelayUse()
        return RelayUse(changed=True, safe=False)

    def alias(self, target: ast.expr, seen: frozenset[ast.AST]) -> RelayUse:
        if not isinstance(target, ast.Name):
            return RelayUse(changed=True, safe=False)
        nodes = self.parents.keys()
        references = [
            node
            for node in nodes
            if isinstance(node, ast.Name) and node.id == target.id
        ]
        if not self.stable(target):
            return RelayUse(changed=True, safe=False)
        uses = [
            self.inspect(node, "return", seen)
            for node in references
            if isinstance(node.ctx, ast.Load)
        ]
        returned = {use.returned for use in uses if use.returned}
        return RelayUse(
            changed=any(use.changed for use in uses),
            safe=len(returned) <= 1 and all(use.safe for use in uses),
            returned=next(iter(returned), "") if len(returned) <= 1 else "?",
            namespace=any(use.namespace for use in uses),
        )

    def stable(self, target: ast.Name) -> bool:
        return sum(
            isinstance(node, ast.Name)
            and node.id == target.id
            and not isinstance(node.ctx, ast.Load)
            for node in self.parents
        ) == 1 and not any(
            isinstance(node, ast.arg)
            and node.arg == target.id
            or isinstance(node, (ast.Global, ast.Nonlocal))
            and target.id in node.names
            for node in self.parents
        )

    def excluded(self, value: ast.AST) -> bool:
        """A literal tuple flag can exclude an alias use for the returned input."""
        child = value
        while (parent := self.parents.get(child)) is not None:
            if isinstance(parent, ast.If):
                test = parent.test
                negated = isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
                if negated and isinstance(test, ast.UnaryOp):
                    test = test.operand
                if isinstance(test, ast.Name):
                    for name, flag in self.conditions:
                        if test.id == name and (
                            child in parent.body
                            and flag == negated
                            or child in parent.orelse
                            and flag != negated
                        ):
                            return True
            child = parent
        return False
