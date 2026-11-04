# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Indexed root-client parameter contracts, independent of return summaries."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from .annotation_proofs import AnnotationProofs
from .expression_names import dotted
from .parameter_usage import parameter_usage

if TYPE_CHECKING:
    from .resolution import Resolver

PARAMETERS: Final = ".@client-parameters"
BOUND_PARAMETERS: Final = ".@bound-client-parameters"
PARAMETER_OWNER: Final = ".@parameter-owner"
INSTANCE_DECORATORS: Final = frozenset(
    {
        "call:functools.cache",
        "call:functools.cached_property",
        "call:functools.lru_cache",
        "module:tenacity.retry",
        "module:typing.override",
        "module:typing_extensions.override",
    }
)


def instance_receiver(
    node: ast.FunctionDef | ast.AsyncFunctionDef, annotations: AnnotationProofs
) -> str:
    """Identity decorators preserve binding only when their imports are proven."""
    positional = [*node.args.posonlyargs, *node.args.args]
    return (
        positional[0].arg
        if positional
        and all(
            annotations.resolver.infer(
                decorator.func if isinstance(decorator, ast.Call) else decorator,
                annotations.env,
            )
            in INSTANCE_DECORATORS
            for decorator in node.decorator_list
        )
        else ""
    )


def parameter_owner(node: ast.AST | None, annotations: AnnotationProofs) -> str:
    """Keep first-party receiver ownership solely for parameter-contract lookup."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return ""
    kind = annotations.resolver.infer(node, annotations.env) if node is not None else ""
    return kind.replace("class:", "instance:", 1) if kind.startswith("class:") else ""


def parameter_contract(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    annotations: AnnotationProofs,
    bound: bool = False,
) -> str:
    """Encode only explicitly proven root parameters; preserve positional-only slots."""
    transparent = INSTANCE_DECORATORS | {
        "call:builtins.staticmethod",
        "call:builtins.classmethod",
    }
    if any(
        annotations.resolver.infer(
            decorator.func if isinstance(decorator, ast.Call) else decorator,
            annotations.env,
        )
        not in transparent
        for decorator in node.decorator_list
    ):
        return ""
    uses = parameter_usage(node, annotations)
    positional = [*node.args.posonlyargs, *node.args.args]
    return ";".join(
        f"{'' if arg in node.args.posonlyargs else arg.arg}={uses[arg.arg]}{index - int(bound)}"
        for index, arg in enumerate(positional)
        if index >= int(bound) and arg.arg in uses
    ) + "".join(
        f";{arg.arg}={uses[arg.arg]}keyword"
        for arg in node.args.kwonlyargs
        if arg.arg in uses
    )


@dataclass(frozen=True)
class ClientArguments:
    """Resolve a call against lexical/imported contracts at its evaluation point."""

    resolver: Resolver
    env: dict[str, str]

    def contract(self, function: ast.expr, suffix: str = PARAMETERS) -> str:
        from .call_sites import BOUND_CALL_PARAMETERS, CALL_PARAMETERS

        name = dotted(function)
        if name + suffix in self.env:
            return self.env[name + suffix]
        if name in self.env:
            return ""
        if not isinstance(function, ast.Attribute):
            return ""
        kind = self.env.get(
            dotted(function.value) + PARAMETER_OWNER
        ) or self.resolver.infer(function.value, self.env)
        kind = kind.removeprefix("parameter-")
        if kind.startswith(("module:", "local-module:")):
            return self.resolver.index.exports.get(kind.split(":", 1)[1], {}).get(
                function.attr + suffix, ""
            )
        if kind.startswith(("class:", "instance:")):
            if kind.endswith(":review"):
                return ""
            owner = kind.split(":")[1]
            if any(
                mutation in {function.attr, owner + "." + function.attr, owner + ".*"}
                for mutation in self.resolver.index.class_mutations
            ):
                return ""
            if suffix == PARAMETERS and kind.startswith("instance:"):
                suffix = BOUND_PARAMETERS
            if suffix == CALL_PARAMETERS and kind.startswith("instance:"):
                suffix = BOUND_CALL_PARAMETERS
            return self.env.get(owner + "." + function.attr + suffix, "")
        return ""

    def arguments(
        self, node: ast.Call, *, safe_only: bool = False, include_relays: bool = False
    ) -> set[ast.expr]:
        """Unpacked calls cannot certify which parameter receives a value."""
        if any(isinstance(arg, ast.Starred) for arg in node.args) or any(
            kw.arg is None for kw in node.keywords
        ):
            return set()
        result: set[ast.expr] = set()
        for entry in filter(None, self.contract(node.func).split(";")):
            name, _, slot = entry.partition("=")
            if safe_only and slot.startswith("!"):
                continue
            if not include_relays and slot.startswith("~"):
                continue
            slot = slot.lstrip("!~")
            slot = slot.rsplit("/", 1)[-1]
            if slot == "any" and not safe_only:
                # An uncertain generated signature supplies review, never ownership.
                result.update(node.args)
                result.update(
                    kw.value for kw in node.keywords if name == "*" or kw.arg == name
                )
                continue
            supplied = [kw.value for kw in node.keywords if kw.arg == name]
            if slot.isdecimal() and int(slot) < len(node.args):
                supplied.append(node.args[int(slot)])
            if len(supplied) == 1:
                result.add(supplied[0])
        return result
