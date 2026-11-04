# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Recognize exact lazy-module patch targets and bounded replacement expressions."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .imports import mock_target_kind
from .mock_proofs import patch_helper

if TYPE_CHECKING:
    from .flow import Flow


@dataclass(frozen=True)
class DoubleTargets:
    flow: Flow

    def kind(self, node: ast.expr | ast.stmt) -> str:
        return self.flow.kinds.get(
            (
                node.lineno,
                node.col_offset,
                node.end_lineno or node.lineno,
                node.end_col_offset or node.col_offset,
            ),
            "",
        )

    def mark(self, node: ast.expr | ast.stmt, kind: str) -> None:
        self.flow.kinds[
            node.lineno,
            node.col_offset,
            node.end_lineno or node.lineno,
            node.end_col_offset or node.col_offset,
        ] = kind

    def factory(self, node: ast.expr, module: bool = False) -> bool:
        """Only binding-proven bare constructors have a shape we can extend."""
        if not isinstance(node, ast.Call) or any(
            kw.arg is None for kw in node.keywords
        ):
            return False
        function = self.kind(node.func).removeprefix("module:").removeprefix("call:")
        if module and function == "types.ModuleType":
            return (
                len(node.args) in {1, 2}
                and not node.keywords
                and all(
                    isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                    for arg in node.args
                )
            )
        if module and function == "types.SimpleNamespace":
            return not node.args and all(kw.arg == "v2" for kw in node.keywords)
        if not module and function == "types.SimpleNamespace":
            return (
                not node.args
                and bool(node.keywords)
                and (
                    all(
                        kw.arg in self.flow.resolver.mapping.moved
                        for kw in node.keywords
                    )
                    or len(node.keywords) == 1
                    and node.keywords[0].arg == "v0"
                )
            )
        mock_factory = function in {
            f"{library}.{helper}"
            for library in ("mock", "unittest.mock")
            for helper in (
                ("Mock", "MagicMock")
                if module
                else ("Mock", "MagicMock", "AsyncMock", "create_autospec")
            )
        }
        if module:
            return (
                mock_factory
                and not node.args
                and all(kw.arg in {"name", "unsafe"} for kw in node.keywords)
            )
        specs = [
            *node.args,
            *(kw.value for kw in node.keywords if kw.arg in {"spec", "spec_set"}),
        ]
        return (
            mock_factory
            and len(specs) <= 1
            and all(self.kind(spec) in {"type:client", "client"} for spec in specs)
            and all(
                kw.arg in {"name", "unsafe", "spec", "spec_set", "instance"}
                for kw in node.keywords
            )
        )

    def returned(self, node: ast.expr) -> ast.Name | None:
        """Peel only literal zero-argument lambdas or simple return-value mocks."""
        if isinstance(node, ast.Lambda) and not (
            node.args.posonlyargs
            or node.args.args
            or node.args.kwonlyargs
            or node.args.vararg
            or node.args.kwarg
        ):
            return node.body if isinstance(node.body, ast.Name) else None
        if isinstance(node, ast.Call):
            function = self.kind(node.func)
            if (
                function
                in {
                    "module:unittest.mock.Mock",
                    "module:unittest.mock.MagicMock",
                    "module:mock.Mock",
                    "module:mock.MagicMock",
                }
                and not node.args
                and len(node.keywords) == 1
            ):
                kw = node.keywords[0]
                if kw.arg == "return_value" and isinstance(kw.value, ast.Name):
                    return kw.value
                if kw.arg == "side_effect":
                    return self.returned(kw.value)
        return None

    def replacement(self, node: ast.Call) -> ast.Name | None:
        """Resolve string/object patch targets through the existing project index."""
        helper = patch_helper(self.kind(node.func))
        if (
            helper not in {"patch", "patch.object", "setattr"}
            or any(isinstance(arg, ast.Starred) for arg in node.args)
            or any(kw.arg is None for kw in node.keywords)
        ):
            return None
        keywords = {kw.arg: kw.value for kw in node.keywords}
        target = keywords.get("target", node.args[0] if node.args else None)
        name = ""
        offset = 1
        if (
            isinstance(target, ast.Constant)
            and isinstance(target.value, str)
            and helper != "patch.object"
        ):
            name = target.value
        elif target is not None:
            offset = 2
            member = keywords.get(
                "attribute" if helper == "patch.object" else "name",
                node.args[1] if len(node.args) > 1 else None,
            )
            kind = self.kind(target)
            if (
                kind.startswith(("module:", "local-module:"))
                and isinstance(member, ast.Constant)
                and isinstance(member.value, str)
            ):
                name = kind.split(":", 1)[1] + "." + member.value
        if not name or mock_target_kind(
            name, self.flow.resolver.mapping, self.flow.context.index
        ) not in {"fn:module:daytona", "fn:module:daytona_sdk"}:
            return None
        if helper == "setattr":
            value = keywords.get(
                "value", node.args[offset] if len(node.args) > offset else None
            )
            return self.returned(value) if value is not None else None
        replacements = [
            kw
            for kw in node.keywords
            if kw.arg in {"new", "return_value", "side_effect"}
        ]
        positional = node.args[offset:]
        if positional:
            return (
                self.returned(positional[0])
                if len(positional) == 1 and not replacements
                else None
            )
        if len(replacements) != 1:
            return None
        replacement = replacements[0]
        if replacement.arg == "return_value":
            return (
                replacement.value if isinstance(replacement.value, ast.Name) else None
            )
        return self.returned(replacement.value)
