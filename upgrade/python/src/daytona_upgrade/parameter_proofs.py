# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Definition-time defaults and framework injection supply review-only evidence."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from .annotation_exports import joined_bindings
from .annotation_proofs import AnnotationProofs
from .fallback_proofs import required_client_annotation
from .generator_proofs import FunctionBindings
from .module_proofs import module_review

if TYPE_CHECKING:
    from .flow import SourceContext


def parameter_bindings(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    annotations: AnnotationProofs,
    runtime: FunctionBindings,
) -> dict[str, str]:
    """Defaults cannot prove a caller supplied the default, but retain its link."""
    resolver = annotations.resolver
    positional = [*node.args.posonlyargs, *node.args.args]
    defaults = {
        arg.arg: value
        for arg, value in zip(
            positional[len(positional) - len(node.args.defaults) :], node.args.defaults
        )
    }
    defaults.update(
        (arg.arg, value)
        for arg, value in zip(node.args.kwonlyargs, node.args.kw_defaults)
        if value is not None
    )
    bindings: dict[str, str] = {}
    for arg in [*positional, *node.args.kwonlyargs]:
        kind = annotations.resolve(arg.annotation)
        default = defaults.get(arg.arg)
        inferred = resolver.infer(default, runtime.definition) if default else ""
        if (
            isinstance(default, ast.Call)
            and resolver.infer(default.func, runtime.definition)
            == "module:fastapi.Depends"
        ):
            factory = (
                default.args[0]
                if default.args
                else next(
                    (key.value for key in default.keywords if key.arg == "dependency"),
                    None,
                )
            )
            inferred = (
                resolver.infer(factory, runtime.definition).removeprefix("fn:")
                if factory is not None
                else ""
            )
        if kind.startswith("instance:") and inferred and inferred != kind:
            kind += ":review"
        if inferred in {"client", "unproven:client"} or module_review(inferred):
            kind = joined_bindings(
                resolver, ({arg.arg: kind}, {arg.arg: inferred})
            ).get(arg.arg, "")
        bindings[arg.arg] = kind
        if kind == "client" and required_client_annotation(
            arg.annotation, resolver, annotations.env
        ):
            bindings[arg.arg + ".@nonnull"] = "client"
    for arg in (node.args.vararg, node.args.kwarg):
        if arg:
            element = (
                annotations.element(arg.annotation) if arg is node.args.vararg else ""
            )
            bindings[arg.arg] = "items:" + element if element else ""
    return bindings


def fixture_bindings(
    node: ast.FunctionDef | ast.AsyncFunctionDef, context: SourceContext
) -> dict[str, str]:
    """Match real fixture declarations in this module or an ancestor conftest."""
    available = {
        name.removeprefix("@fixture."): kind
        for module in sorted(
            context.index.exports,
            key=lambda module: (module == context.module, module.count(".")),
        )
        if module == context.module
        or module == "conftest"
        or module.endswith(".conftest")
        and context.module.startswith(module.removesuffix("conftest"))
        for name, kind in context.index.exports[module].items()
        if name.startswith("@fixture.")
    }
    return {
        arg.arg: available[arg.arg]
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if arg.arg in available
    }
