# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Review-only return/yield summaries using the ordinary sequential flow engine."""

from __future__ import annotations

import ast
from urllib.parse import quote

from .annotation_proofs import AnnotationProofs
from .call_sites import record_arguments
from .collection_proofs import tuple_members
from .flow import Flow, SourceContext
from .generator_proofs import FunctionBindings
from .import_only_clients import IMPORT_ONLY
from .mock_inference import MockInference
from .module_proofs import REVIEW_MODULE, REVIEW_MODULE_CLIENT, ROOT_MODULES
from .parameter_proofs import parameter_bindings
from .proof_mutations import invalidate
from .resolution import Resolver


class ReturnFlow(Flow):
    """Collect result evidence without admitting nested functions' returns."""

    returned: bool = False
    yielded: bool = False
    generator: bool = False
    module_returned: bool = False
    module_exact: bool = True
    tuple_expected: tuple[str, ...] = ()
    tuple_exact: bool = True
    instance_expected: str = ""
    instance_conflict: bool = False

    def __init__(self, resolver: Resolver, context: SourceContext) -> None:
        super().__init__(resolver, context)
        self.instance_returns: set[str] = set()
        self.result_kinds: set[str] = set()
        self.yield_kinds: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        # Summaries need binding effects, not source-text call-site diagnostics.
        record_arguments(node, self)
        self.generic_visit(node)
        MockInference(self.resolver, self.env).configure(node)
        self.bind(node, "")

    def visit_Return(self, node: ast.Return) -> None:
        self.generic_visit(node)
        if not self.scope:
            kind = self.resolver.infer(node.value, self.env) if node.value else ""
            self.result_kinds.add(kind)
            self.instance_returns.add(kind if kind.startswith("instance:") else "")
            self.instance_conflict |= (
                bool(self.instance_expected)
                and (kind.startswith("instance:") or kind == "external-instance")
                and kind != self.instance_expected
            )
            if self.tuple_expected:
                self.tuple_exact &= (
                    isinstance(node.value, ast.Tuple)
                    and len(node.value.elts) == len(self.tuple_expected)
                    and all(
                        expected != "unproven:client"
                        or self.resolver.infer(value, self.env) == "client"
                        for value, expected in zip(node.value.elts, self.tuple_expected)
                    )
                )
            self.module_returned |= kind in ROOT_MODULES or kind in {
                REVIEW_MODULE,
                REVIEW_MODULE_CLIENT,
            }
            self.module_exact &= kind in ROOT_MODULES
            self.returned |= kind in {
                "client",
                "unproven:client",
                REVIEW_MODULE_CLIENT,
            }

    def visit_Yield(self, node: ast.Yield | ast.YieldFrom) -> None:
        self.generic_visit(node)
        if not self.scope:
            self.generator = True
            if node.value is not None:
                kind = self.resolver.infer(node.value, self.env)
                if isinstance(node, ast.YieldFrom):
                    kind = kind.removeprefix("unproven:").removeprefix("items:")
                    if kind.startswith("mock:"):
                        kind = "mock:uncertain"
                self.yield_kinds.add(kind)
                self.yielded |= kind in {"client", "unproven:client"}
            else:
                self.yield_kinds.add("")

    visit_YieldFrom = visit_Yield

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # A lambda's defaults execute here, but its body is a separate return/yield scope.
        for default in [*node.args.defaults, *(d for d in node.args.kw_defaults if d)]:
            self.visit(default)


def returned_kind(
    annotations: AnnotationProofs,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    runtime: FunctionBindings,
) -> str:
    """Prove only all-module returns; client returns remain review evidence."""
    resolver = annotations.resolver
    flow = ReturnFlow(resolver, SourceContext("", "", resolver.index))
    flow.env = runtime.execution.copy()
    for name in flow.assigned_names(node):
        if "." not in name:
            invalidate(flow.env, name)
    flow.env.update(parameter_bindings(node, annotations, runtime))
    expected = annotations.resolve(node.returns)
    if expected.startswith("instance:"):
        flow.instance_expected = expected
    if expected.startswith("tuple:") and "unproven:client" in tuple_members(expected):
        flow.tuple_expected = tuple_members(expected)
        locals_ = flow.assigned_names(node) | {
            arg.arg
            for arg in [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
                *(
                    arg
                    for arg in (node.args.vararg, node.args.kwarg)
                    if arg is not None
                ),
            ]
        }
        for marker, kind in runtime.execution.items():
            if (
                marker.startswith(IMPORT_ONLY)
                and marker.removeprefix(IMPORT_ONLY) not in locals_
            ):
                flow.env[marker.removeprefix(IMPORT_ONLY)] = kind
    reaches = flow.visit_suite(node.body)
    results = flow.yield_kinds if flow.generator else flow.result_kinds
    if any(kind.startswith(("mock:", "mock-class:")) for kind in results) and any(
        resolver.infer(
            decorator.func if isinstance(decorator, ast.Call) else decorator,
            runtime.definition,
        )
        == "module:pytest.fixture"
        for decorator in node.decorator_list
    ):
        exact = len(results) == 1 and (flow.generator or not reaches)
        kind = next(iter(results)) if exact else "mock:uncertain"
        return ("items:" if flow.generator else "") + kind
    if flow.instance_conflict:
        return expected + ":review"
    if flow.generator:
        return "items:unproven:client" if flow.yielded else ""
    if flow.module_returned:
        if flow.returned:
            return REVIEW_MODULE_CLIENT
        return (
            "module:daytona"
            if flow.module_exact and not reaches and isinstance(node, ast.FunctionDef)
            else REVIEW_MODULE
        )
    if flow.tuple_expected and flow.tuple_exact and not reaches:
        return "tuple:" + "|".join(
            quote("client" if kind == "unproven:client" else kind, safe="")
            for kind in flow.tuple_expected
        )
    if (
        len(flow.instance_returns) == 1
        and "" not in flow.instance_returns
        and not reaches
    ):
        return "parameter-" + next(iter(flow.instance_returns))
    return "unproven:client" if flow.returned else ""
