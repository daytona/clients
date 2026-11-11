# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Expression inference for the deliberately bounded Python flow analysis."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import cached_property

from ._project_index_model import ProjectIndex as ProjectIndex
from .annotation_proofs import AnnotationProofs
from .call_proofs import CallProofs
from .collection_proofs import (
    literal_collection_kind,
    subscript_kind,
)
from .context_managers import context_attribute
from .expression_names import dotted as dotted
from .expression_names import looks_like_client as looks_like_client
from .external_calls import foreign_constructor as foreign_constructor
from .field_safety import intercepted_member
from .function_proofs import FunctionProofs
from .generator_proofs import FunctionBindings
from .instance_fields import instance_member
from .mock_inference import MockInference
from .models import Mapping
from .subclasses import member_receiver


@dataclass(frozen=True)
class Resolver:
    mapping: Mapping
    index: ProjectIndex

    @cached_property
    def functions(self) -> FunctionProofs:
        return FunctionProofs(self)

    def imported(self, module: str, name: str) -> str:
        if self.index.owns(module):
            return self.index.exports.get(module, {}).get(name, "")
        if module in ("logging", "abc"):
            return "module:" + module + "." + name
        if module == "unittest" and name == "mock":
            return "module:unittest.mock"
        if module == "types" and name in {"SimpleNamespace", "ModuleType"}:
            return "module:types." + name
        if module in {"unittest.mock", "mock"} and name != "patch":
            return "module:" + module + "." + name
        if module == "builtins" and name in {"dict", "Exception", "BaseException"}:
            return "module:builtins." + name
        if module == "typing" and name == "TYPE_CHECKING":
            return "typing:TYPE_CHECKING"
        if (module, name) in (
            ("unittest.mock", "patch"),
            ("mock", "patch"),
            ("importlib", "import_module"),
            ("dataclasses", "field"),
            ("dataclasses", "dataclass"),
            ("builtins", "setattr"),
            ("builtins", "print"),
            ("builtins", "repr"),
            ("builtins", "str"),
            ("builtins", "getattr"),
            ("builtins", "hasattr"),
            ("json", "dumps"),
            ("functools", "cache"),
            ("functools", "lru_cache"),
            ("functools", "wraps"),
            ("functools", "cached_property"),
            ("builtins", "staticmethod"),
            ("builtins", "classmethod"),
            ("builtins", "property"),
        ):
            return "call:" + module + "." + name
        if (
            module in self.mapping.modules
            or module.startswith(tuple(old + "." for old in self.mapping.modules))
            and not module.startswith("daytona.v2")
        ):
            for kind, receiver in self.mapping.receivers.items():
                if name in receiver.types:
                    return "type:" + kind
            if (
                self.mapping.dockerfile_image is not None
                and name == self.mapping.dockerfile_image.type
            ):
                # The 0.x Image class, as `import daytona; daytona.Image` reads
                # it: its Dockerfile call is then provable (dockerfile_image).
                return "module:" + module + "." + name
            if name == "DaytonaConfig":
                return "type:config"
            if name == "common" and module in self.mapping.modules:
                return "module:" + module + ".common"
            if (
                name in self.mapping.root_names
                and name not in self.mapping.root_imports
            ) or name in self.mapping.root_alias_categories:
                # A shared error, or a former root alias naming one.
                return "module:" + module + "." + name
        return self.index.exports.get(module, {}).get(name, "")

    def function_kind(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        annotations: dict[str, str],
        runtime: FunctionBindings,
    ) -> str:
        return self.functions.kind(node, annotations, runtime)

    def annotation(self, node: ast.AST | None, env: dict[str, str]) -> str:
        return AnnotationProofs(self, env).resolve(node)

    def call_kind(self, node: ast.Call, env: dict[str, str]) -> str:
        return CallProofs(self, env).resolve(node, self.infer(node.func, env))

    def infer(self, node: ast.AST, env: dict[str, str]) -> str:
        # Re-inference uses the final environment, not each child's evaluation state.
        # A nested rebinding can therefore never certify the enclosing expression.
        if not isinstance(node, ast.NamedExpr) and any(
            isinstance(child, ast.NamedExpr) for child in ast.walk(node)
        ):
            return ""
        kind = MockInference(self, env).member(node) or self.expression_kind(node, env)
        if kind == "external-instance" and "daytona" in dotted(node).lower():
            return "unproven:client"
        # Module attribute proofs may come from the project index rather than env.
        # Without alias identities, a write fences all future container proofs;
        # review-only evidence certifies nothing, so it survives the fence.
        fenced = kind.startswith("items:") and kind != "items:unproven:client"
        return "" if fenced and "@container-write" in env else kind

    def expression_kind(self, node: ast.AST, env: dict[str, str]) -> str:
        if isinstance(node, ast.Name):
            return env.get(node.id, "")
        if isinstance(node, ast.Await):
            return self.infer(node.value, env)
        if isinstance(node, ast.NamedExpr):
            return self.infer(node.value, env)
        if isinstance(node, (ast.IfExp, ast.BoolOp)):
            from .fallback_proofs import fallback_kind

            return fallback_kind(node, self, env)
        if isinstance(node, ast.Attribute):
            if (intercepted := intercepted_member(node, self, env)) is not None:
                return intercepted
            name = dotted(node)
            if env.get(name):
                return env[name]
            if wildcard := env.get(dotted(node.value) + ".*", ""):
                return wildcard
            if name in env:
                return env[name]
            parent = member_receiver(self.infer(node.value, env), node.attr)
            if context := context_attribute(parent, node.attr):
                return context
            if parent == "project-member":
                return parent
            if parent in (
                "call:unittest.mock.patch",
                "call:mock.patch",
                "unbound:patch",
            ) and node.attr in (
                "object",
                "multiple",
            ):
                return parent + "." + node.attr
            if parent == "logger":
                return "call:logging." + node.attr
            if parent.startswith(("module:", "local-module:")):
                prefix, module = parent.split(":", 1)
                value = (
                    self.index.exports.get(module, {}).get(node.attr, "")
                    if prefix == "local-module"
                    else self.imported(module, node.attr)
                )
                return (
                    value
                    or foreign_constructor(module, node.attr, self.index)
                    or prefix + ":" + module + "." + node.attr
                )
            if parent == "client" and node.attr == "v0":
                return "client.v0"
            if parent == "client" and node.attr == "v2":
                return "v2"
            for kind, receiver in self.mapping.receivers.items():
                if (
                    receiver.parent
                    == parent.removesuffix(".v0").removeprefix("unproven:")
                    and receiver.attr == node.attr
                ):
                    return kind
            if parent.startswith(("instance:", "class:")):
                return instance_member(node, self, env)
            return ""
        if isinstance(node, ast.Call):
            if (
                dotted(node.func) == "super"
                and "super" not in env
                and "super" not in self.index.builtin_writes
                and env.get("self", "").startswith("self-subclass:")
            ):
                return "unproven:client"
            return self.call_kind(node, env)
        if isinstance(node, ast.Subscript):
            return subscript_kind(node, lambda value: self.infer(value, env))
        if isinstance(node, ast.Constant):
            return "non-client"
        if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
            return (
                literal_collection_kind(node, lambda value: self.infer(value, env))
                or "non-client"
            )
        return literal_collection_kind(node, lambda value: self.infer(value, env))


def relative_module(current: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    prefix = current.split(".")[: -node.level]
    return ".".join([*prefix, *([node.module] if node.module else [])])
