# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Call-result proofs reuse the callee resolved by the active resolver."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .collection_proofs import dictionary_members, literal_collection_kind
from .config_members import factory_kind
from .context_managers import context_call
from .dockerfile_images import DOCKERFILE_IMAGE, is_dockerfile_image_call
from .expression_names import dotted
from .external_calls import external_call_kind
from .field_arguments import instance_kind
from .mock_inference import MockInference
from .module_proofs import module_review
from .subclasses import call_result, member_receiver

if TYPE_CHECKING:
    from .resolution import Resolver


@dataclass(frozen=True)
class CallProofs:
    """Resolve ordinary call results after any specialized callee handling."""

    resolver: Resolver
    env: dict[str, str]

    def resolve(self, node: ast.Call, function: str) -> str:
        resolver, env = self.resolver, self.env
        if mock := MockInference(resolver, env).call(node, function):
            return mock
        if function == "annotation:unproven:client":
            return "unproven:client"
        if (
            dotted(node.func) == "dict"
            and "dict" not in env
            and "dict" not in resolver.index.builtin_writes
            or function == "module:builtins.dict"
        ):
            return literal_collection_kind(
                node, lambda value: resolver.infer(value, env)
            )
        if context := context_call(
            function, node, lambda value: resolver.infer(value, env)
        ):
            return context
        if function == "module:logging.getLogger":
            return "logger"
        if is_dockerfile_image_call(function, resolver.mapping):
            return DOCKERFILE_IMAGE
        if (
            function == "call:importlib.import_module"
            and len(node.args) == 1
            and not node.keywords
        ):
            module = node.args[0]
            if (
                isinstance(module, ast.Constant)
                and isinstance(module.value, str)
                and module.value in resolver.mapping.modules
                and not resolver.index.owns(module.value)
            ):
                return "module:" + module.value
        if function.startswith(("type:", "fn:")):
            return call_result(function)
        if function.startswith("class:"):
            return instance_kind(node, resolver, env)
        if isinstance(node.func, ast.Attribute):
            if config := factory_kind(node.func, resolver.infer(node.func.value, env)):
                return config
            receiver = member_receiver(
                resolver.infer(node.func.value, env), node.func.attr
            ).removesuffix(".v0")
            key, value = dictionary_members(receiver)
            if key or value:
                member = {
                    "keys": key,
                    "values": value,
                    "items": "dict-pair:" + receiver,
                }.get(node.func.attr, "")
                if node.func.attr in {"keys", "values", "items"}:
                    return "items:" + member if member else ""
            for kind, definition in resolver.mapping.receivers.items():
                for identifier in definition.produced_by:
                    owner, _, method = identifier.rpartition(".")
                    suggestion = next(
                        (s for s in resolver.mapping.suggestions if s.id == identifier),
                        None,
                    )
                    method = suggestion.method if suggestion else method
                    if receiver == owner and node.func.attr == method:
                        return "items:" + kind if method == "list" else kind
        return next(
            (
                review
                for argument in [*node.args, *(kw.value for kw in node.keywords)]
                if (review := module_review(resolver.infer(argument, env)))
            ),
            "",
        ) or external_call_kind(function, dotted(node.func), resolver.index)
