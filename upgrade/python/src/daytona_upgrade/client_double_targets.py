# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Propagate indexed client contracts through bounded literal mock replacements."""

from __future__ import annotations

import ast

from .call_sites import CALL_PARAMETERS
from .imports import mock_target_kind
from .local_double_targets import DoubleTargets
from .mock_proofs import patch_helper


class ClientDoubleTargets(DoubleTargets):
    """Patch return contracts complement the flow's explicit client argument slots."""

    def member(self, kind: str, name: str) -> str:
        if not kind.startswith(("class:", "instance:")) or kind.endswith(":review"):
            return ""
        key = kind.split(":")[1] + "." + name
        return self.flow.env.get(key, "") or next(
            (
                exports[key]
                for exports in self.flow.context.index.exports.values()
                if key in exports
            ),
            "",
        )

    def target(self, node: ast.Call) -> tuple[str, str, int] | None:
        """Resolve the patched declaration independently of replacement syntax."""
        helper = patch_helper(self.kind(node.func))
        if helper not in {"patch", "patch.object", "setattr"}:
            return None
        keywords = {kw.arg: kw.value for kw in node.keywords}
        target = keywords.get("target", node.args[0] if node.args else None)
        kind = ""
        contract = ""
        offset = 1
        if isinstance(target, ast.Constant) and isinstance(target.value, str):
            for module, exports in self.flow.context.index.exports.items():
                if target.value.startswith(module + "."):
                    contract = (
                        exports.get(
                            target.value.removeprefix(module + ".") + CALL_PARAMETERS,
                            "",
                        )
                        or exports.get(target.value + CALL_PARAMETERS, "")
                        or contract
                    )
            kind = mock_target_kind(
                target.value, self.flow.resolver.mapping, self.flow.context.index
            )
            if not kind:
                kind = next(
                    (
                        exports[target.value]
                        for exports in self.flow.context.index.exports.values()
                        if target.value in exports
                    ),
                    "",
                )
        elif target is not None:
            offset = 2
            member = keywords.get(
                "attribute" if helper == "patch.object" else "name",
                node.args[1] if len(node.args) > 1 else None,
            )
            if isinstance(member, ast.Constant) and isinstance(member.value, str):
                kind = self.member(self.kind(target), member.value)
                contract = self.member(
                    self.kind(target), member.value + CALL_PARAMETERS
                )
                if self.kind(target).startswith(("module:", "local-module:")):
                    kind = mock_target_kind(
                        self.kind(target).split(":", 1)[1] + "." + member.value,
                        self.flow.resolver.mapping,
                        self.flow.context.index,
                    )
                    module = self.kind(target).split(":", 1)[1]
                    contract = self.flow.context.index.exports.get(module, {}).get(
                        member.value + CALL_PARAMETERS, ""
                    )
        if not kind.startswith(("fn:client", "fn:instance:")):
            return None
        return kind, contract.rsplit(":", 1)[0], offset

    def patch(
        self, node: ast.Call, *, safe_only: bool = False
    ) -> tuple[ast.expr, str] | None:
        target = self.target(node)
        if target is None:
            return None
        kind, contract, offset = target
        helper = patch_helper(self.kind(node.func))
        keywords = {kw.arg: kw.value for kw in node.keywords}
        if any(isinstance(arg, ast.Starred) for arg in node.args) or None in keywords:
            return None
        if (
            safe_only
            and kind == "fn:client"
            and contract not in self.flow.context.index.adaptable_factories
        ):
            return None
        if helper != "setattr" and "return_value" in keywords:
            return keywords["return_value"], kind.removeprefix("fn:")
        value = keywords.get(
            "value" if helper == "setattr" else "new",
            node.args[offset] if len(node.args) > offset else None,
        )
        if value is None:
            value = keywords.get("side_effect")
        return (value, kind) if value is not None else None

    def expand(self, expected: dict[ast.expr, str]) -> None:
        """Expand only transparent factory literals, never arbitrary object graphs."""
        for node, kind in tuple(expected.items()):
            if kind.startswith("fn:"):
                if isinstance(node, ast.Call) and self.kind(node.func) in {
                    f"module:{module}.{name}"
                    for module in ("mock", "unittest.mock")
                    for name in ("Mock", "MagicMock", "AsyncMock")
                }:
                    if node.args or any(
                        keyword.arg in {None, "side_effect"}
                        for keyword in node.keywords
                    ):
                        expected[node] = "unresolved-factory"
                    else:
                        for keyword in node.keywords:
                            if keyword.arg == "return_value":
                                expected[keyword.value] = kind.removeprefix("fn:")
                if isinstance(node, ast.Lambda) and not (
                    node.args.posonlyargs
                    or node.args.args
                    or node.args.kwonlyargs
                    or node.args.vararg
                    or node.args.kwarg
                ):
                    expected[node.body] = kind.removeprefix("fn:")
            elif (
                kind.startswith("instance:")
                and isinstance(node, ast.Call)
                and (
                    self.kind(node.func) == "module:types.SimpleNamespace"
                    and not node.args
                    and all(kw.arg is not None for kw in node.keywords)
                )
            ):
                for keyword in node.keywords:
                    member = self.member(kind, keyword.arg or "")
                    if member.startswith(("fn:client", "fn:instance:")):
                        expected[keyword.value] = member

    def clients(self, *, safe_only: bool = False) -> set[ast.expr]:
        """Require exclusive local ownership before following a named mock container."""
        from ._scopes import ScopeWrites
        from .local_doubles import scope_nodes

        expected = dict.fromkeys(
            self.flow.adaptable_arguments if safe_only else self.flow.client_arguments,
            "client",
        )
        for node in ast.walk(self.flow.module):
            if isinstance(node, ast.Call) and (
                patch := self.patch(node, safe_only=safe_only)
            ):
                expected[patch[0]] = patch[1]
        scopes = []
        for scope in ast.walk(self.flow.module):
            if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nodes = [
                    child
                    for statement in scope.body
                    for child in scope_nodes(statement)
                ]
                writes = ScopeWrites()
                for statement in scope.body:
                    writes.visit(statement)
                scopes.append((scope, nodes, writes))
        while True:
            before = len(expected)
            self.expand(expected)
            for scope, nodes, writes in scopes:
                for node in nodes:
                    if not (
                        isinstance(node, ast.Assign)
                        and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)
                    ):
                        continue
                    name = node.targets[0].id
                    if (
                        sum(binding == name for binding, _ in writes.events) != 1
                        or name in writes.shared
                    ):
                        continue
                    references = [
                        child
                        for statement in scope.body
                        for child in ast.walk(statement)
                        if isinstance(child, ast.Name)
                        and child.id == name
                        and isinstance(child.ctx, ast.Load)
                    ]
                    kinds = {expected.get(reference, "") for reference in references}
                    if (
                        len(kinds) == 1
                        and "" not in kinds
                        and all(
                            reference.lineno > node.lineno for reference in references
                        )
                    ):
                        expected[node.value] = kinds.pop()
            if len(expected) == before:
                break
        return {
            node
            for node, kind in expected.items()
            if kind in {"client", "unresolved-factory"}
        }
