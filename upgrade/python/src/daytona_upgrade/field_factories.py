# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Bounded default-factory proof with review-only fallback provenance."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from .field_initialization import FIELD_HELPERS

if TYPE_CHECKING:
    from .classflow import AttributeWrites


def factory_kind(node: ast.AnnAssign, writes: AttributeWrites) -> str | None:
    """Only direct SDK constructors establish a factory's instance value."""
    from .annotation_proofs import AnnotationProofs
    from .proof_mutations import invalidate

    call = node.value
    if not isinstance(call, ast.Call):
        return None
    framework = FIELD_HELPERS.get(writes.resolver.infer(call.func, writes.env), "")
    if not framework or framework == "dataclass" and not writes.dataclass:
        return None
    factories = [
        kw.value
        for kw in call.keywords
        if kw.arg == ("factory" if framework == "attrs" else "default_factory")
    ]
    options = (
        {
            "factory",
            "default",
            "repr",
            "eq",
            "order",
            "hash",
            "kw_only",
            "metadata",
            "init",
            "alias",
        }
        if framework == "attrs"
        else {"default_factory", "repr", "description", "title"}
    )
    if framework == "attrs":
        for keyword in call.keywords:
            if keyword.arg != "default" or not isinstance(keyword.value, ast.Call):
                continue
            default = keyword.value
            if (
                writes.resolver.infer(default.func, writes.env)
                in {"module:attrs.Factory", "module:attr.Factory"}
                and len(default.args) == 1
                and not default.keywords
            ):
                factories.append(default.args[0])
    annotation = writes.resolver.annotation(node.annotation, writes.annotation_env())
    for factory in factories:
        direct = writes.resolver.infer(factory, writes.env) == "type:client"
        if isinstance(factory, ast.Lambda):
            args = factory.args
            # A factory lambda runs later, outside the class's lexical namespace.
            execution = writes.class_lexical.copy()
            for name in writes.deferred.get(factory, set()):
                invalidate(execution, name)
            direct = (
                not (
                    args.posonlyargs
                    or args.args
                    or args.kwonlyargs
                    or args.vararg
                    or args.kwarg
                )
                and isinstance(factory.body, ast.Call)
                and writes.resolver.infer(factory.body.func, execution) == "type:client"
                and writes.resolver.infer(factory.body, execution) == "client"
            )
        if (
            direct
            and len(factories) == 1
            and not call.args
            and all(kw.arg is not None for kw in call.keywords)
            and writes.env.get("@default-init") == framework
            and (
                framework == "dataclass"
                or annotation == "client"
                and all(kw.arg in options for kw in call.keywords)
                and all(
                    isinstance(kw.value, ast.Constant)
                    and (
                        type(kw.value.value) is bool
                        if kw.arg in {"init", "kw_only"}
                        else isinstance(kw.value.value, str)
                    )
                    for kw in call.keywords
                    if kw.arg in {"init", "kw_only", "alias"}
                )
            )
        ):
            return "client"
    linked = annotation in {"client", "unproven:client"} or any(
        AnnotationProofs(writes.resolver, writes.annotation_env()).mentions_client(
            factory
        )
        or any(
            writes.resolver.infer(child, writes.env)
            in {
                "client",
                "type:client",
                "unproven:client",
                "fn:client",
                "fn:unproven:client",
            }
            for child in ast.walk(factory)
        )
        for factory in factories
    )
    return "unproven:client" if linked else ""
