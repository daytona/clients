# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Bounded annotation evidence, including module aliases and iterable elements."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .annotation_flow import annotation_type_nodes
from .collection_proofs import dictionary_kind, tuple_kind
from .external_calls import unresolved_client_annotation

if TYPE_CHECKING:
    from .resolution import Resolver


@dataclass(frozen=True)
class AnnotationProofs:
    """Resolve type syntax without treating alias metadata as runtime instances."""

    resolver: Resolver
    env: dict[str, str]

    def resolve(self, node: ast.AST | None, depth: int = 0) -> str:
        from .import_only_clients import IMPORT_ONLY

        if node is None:
            return ""
        # Both strings and alias chains consume the budget; cycles never recurse forever.
        if depth >= 32:
            return "unproven:client"
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return self.text(node.value, depth + 1)
        value = self.resolver.infer(node, self.env)
        if (
            value == "annotation:unproven:client"
            and isinstance(node, ast.Name)
            and self.env.get(IMPORT_ONLY + node.id) == "type:client"
        ):
            return "client"
        if value.startswith(("type:", "annotation:")):
            return value.split(":", 1)[1]
        if value.startswith("class:"):
            return "instance:" + value.removeprefix("class:")
        if value.startswith("alias:"):
            text = value.removeprefix("alias:")
            return self.text(text, depth + 1) or (
                "unproven:client" if self.mentions_client(node) else ""
            )
        if uncertain := unresolved_client_annotation(value, node):
            return uncertain
        if isinstance(node, ast.Subscript):
            wrapper = self.resolver.infer(node.value, self.env)
            # Existing nullable/Annotated syntax remains supported on the 3.9 floor.
            name = ast.unparse(node.value).split(".")[-1]
            builtin_type = (
                isinstance(node.value, ast.Name)
                and name == "type"
                and name not in self.env
                and name not in self.resolver.index.builtin_writes
            )
            if builtin_type or wrapper in {
                f"module:{module}.{name}"
                for module in ("typing", "typing_extensions", "builtins")
                for name in ("Type", "type")
            }:
                kind = self.resolve(node.slice, depth + 1)
                return "type:client" if kind == "client" else "non-client"
            if wrapper in {
                f"module:{module}.Callable"
                for module in ("typing", "typing_extensions", "collections.abc")
            }:
                return "non-client"
            if (
                wrapper
                in {
                    f"module:{module}.{generator}"
                    for module in ("typing", "typing_extensions", "collections.abc")
                    for generator in ("Generator", "AsyncGenerator")
                }
                and isinstance(node.slice, ast.Tuple)
                and node.slice.elts
            ):
                kind = self.element(node.slice.elts[0], depth + 1)
                return "items:" + kind if kind else ""
            if wrapper in {
                "module:typing.Optional",
                "module:typing.Union",
                "module:typing_extensions.Optional",
                "module:typing_extensions.Union",
            }:
                return self.union(node.slice, depth + 1)
            if wrapper in {
                "module:typing.Annotated",
                "module:typing_extensions.Annotated",
            }:
                return self.resolve(
                    node.slice.elts[0]
                    if isinstance(node.slice, ast.Tuple)
                    else node.slice,
                    depth + 1,
                )
            if self.collection(node.value):
                element = node.slice
                if (
                    name == "tuple"
                    or wrapper
                    in {
                        "module:typing.Tuple",
                        "module:typing_extensions.Tuple",
                        "module:builtins.tuple",
                    }
                ) and isinstance(element, ast.Tuple):
                    if (
                        len(element.elts) == 2
                        and isinstance(element.elts[1], ast.Constant)
                        and element.elts[1].value is Ellipsis
                    ):
                        element = element.elts[0]
                    else:
                        kinds = tuple(
                            self.element(part, depth + 1) for part in element.elts
                        )
                        if len(set(kinds)) > 1:
                            return tuple_kind(kinds)
                kind = self.element(element, depth + 1)
                return "items:" + kind if kind else ""
            # Unsupported client-bearing containers can be reviewed, never redirected.
            if (
                name in {"dict", "Dict", "Mapping", "MutableMapping"}
                and isinstance(node.slice, ast.Tuple)
                and len(node.slice.elts) == 2
            ):
                return dictionary_kind(
                    *(self.element(element, depth + 1) for element in node.slice.elts)
                )
            if self.mentions_client(node.slice):
                return (
                    "client-dict"
                    if name in {"dict", "Dict", "Mapping", "MutableMapping"}
                    else "unproven:client"
                )
            return ""
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            return self.union(node, depth + 1)
        if isinstance(node, ast.Tuple):
            return self.union(node, depth + 1)
        return ""

    def element(self, node: ast.AST | None, depth: int = 0) -> str:
        """Retain uncertain client evidence when an element type is not homogeneous."""
        return self.resolve(node, depth) or (
            "unproven:client" if node is not None and self.mentions_client(node) else ""
        )

    def text(self, text: str, depth: int) -> str:
        try:
            return self.resolve(ast.parse(text, mode="eval").body, depth)
        except SyntaxError:
            return "unproven:client" if "daytona" in text.lower() else ""

    def union(self, node: ast.AST, depth: int) -> str:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            variants = [node.left, node.right]
        elif isinstance(node, ast.Tuple):
            variants = node.elts
        else:
            variants = [node]
        kinds = [
            self.resolve(variant, depth)
            for variant in variants
            if not (isinstance(variant, ast.Constant) and variant.value is None)
        ]
        if "unproven:client" in kinds:
            return "unproven:client"
        return (
            kinds[0]
            if kinds and kinds[0] and all(kind == kinds[0] for kind in kinds)
            else ""
        )

    def collection(self, node: ast.AST) -> bool:
        """Only known iterable type constructors establish homogeneous element proof."""
        value = self.resolver.infer(node, self.env)
        if isinstance(node, ast.Name) and node.id in {
            "list",
            "tuple",
            "set",
            "frozenset",
        }:
            return (
                node.id not in self.env
                and node.id not in self.resolver.index.builtin_writes
            )
        return value in {
            f"module:{module}.{name}"
            for module in ("typing", "typing_extensions", "collections.abc", "builtins")
            for name in (
                "Sequence",
                "Iterable",
                "Iterator",
                "AsyncIterator",
                "AsyncIterable",
                "Collection",
                "List",
                "Tuple",
                "Set",
                "FrozenSet",
                "list",
                "tuple",
                "set",
                "frozenset",
            )
        }

    def mentions_client(self, node: ast.AST) -> bool:
        """Follow alias references for review evidence without trusting unknown wrappers."""
        pending = [node]
        seen: set[str] = set()
        for _ in range(128):
            if not pending:
                return False
            current = pending.pop()
            for child in annotation_type_nodes(
                current, lambda expression: self.resolver.infer(expression, self.env)
            ):
                value = self.resolver.infer(child, self.env)
                if value in {
                    "type:client",
                    "annotation:client",
                    "annotation:unproven:client",
                    "unproven:client",
                }:
                    return True
                if (
                    isinstance(child, (ast.Name, ast.Attribute))
                    and "daytona" in ast.unparse(child).lower()
                ):
                    return True
                if isinstance(child, ast.Call):
                    pending.extend(child.args)
                    pending.extend(keyword.value for keyword in child.keywords)
                text = (
                    child.value
                    if isinstance(child, ast.Constant) and isinstance(child.value, str)
                    else value.removeprefix("alias:")
                    if value.startswith("alias:")
                    else ""
                )
                if text and text not in seen:
                    seen.add(text)
                    try:
                        pending.append(ast.parse(text, mode="eval").body)
                    except SyntaxError:
                        if "daytona" in text.lower():
                            return True
        return bool(pending)
