# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Cached function-result evidence, separated from explicit return-type proofs."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from .annotation_proofs import AnnotationProofs
from .generator_proofs import FunctionBindings, generator_kind

if TYPE_CHECKING:
    from .resolution import Resolver


class FunctionProofs:
    """Accumulate summaries once per function and execution-binding context."""

    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver
        self.summaries: dict[
            ast.AST,
            dict[tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]], str],
        ] = {}

    def kind(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        annotations: dict[str, str],
        runtime: FunctionBindings,
    ) -> str:
        """Only annotations and the existing generator rules establish proofs."""
        resolver = self.resolver
        transparent = {
            "module:typing.override",
            "module:typing_extensions.override",
            "call:functools.cache",
            "call:functools.lru_cache",
            "call:functools.wraps",
            "call:functools.cached_property",
            "call:builtins.staticmethod",
            "call:builtins.classmethod",
            "call:builtins.property",
        }
        kind = resolver.annotation(node.returns, annotations)
        fixture = any(
            resolver.infer(
                decorator.func if isinstance(decorator, ast.Call) else decorator,
                runtime.definition,
            )
            == "module:pytest.fixture"
            for decorator in node.decorator_list
        )
        if fixture:
            result = self.result(node, annotations, runtime)
            if result.removeprefix("items:").startswith(("mock:", "mock-class:")):
                kind = result
                transparent.add("module:pytest.fixture")
        if kind.startswith("instance:"):
            result = self.result(node, annotations, runtime)
            if result.endswith(":review"):
                kind = result
        if kind.startswith("tuple:"):
            result = self.result(node, annotations, runtime)
            if result.startswith("tuple:"):
                kind = result
        if not kind:
            result = self.result(node, annotations, runtime)
            if node.returns is None or "module:daytona" in result:
                kind = result
        if any(
            resolver.infer(
                decorator.func if isinstance(decorator, ast.Call) else decorator,
                runtime.definition,
            )
            not in transparent
            for decorator in node.decorator_list
        ):
            if kind.removeprefix("items:").startswith(("mock:", "mock-class:")):
                return "mock:uncertain"
            if kind.startswith("items:"):
                return "items:unproven:" + kind.removeprefix("items:").removeprefix(
                    "unproven:"
                )
            return "unproven:" + kind.removeprefix("unproven:") if kind else ""
        return kind

    def result(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        annotations: dict[str, str],
        runtime: FunctionBindings,
    ) -> str:
        """Cache generator proofs and may-evidence, with a recursion sentinel."""
        from .return_proofs import returned_kind

        cache = self.summaries.setdefault(node, {})
        key = (
            tuple(sorted(runtime.execution.items())),
            tuple(sorted(annotations.items())),
        )
        if key not in cache:
            cache[key] = ""
            cache[key] = generator_kind(
                AnnotationProofs(self.resolver, annotations), node, runtime.execution
            ) or returned_kind(
                AnnotationProofs(self.resolver, annotations), node, runtime
            )
        return cache[key]
