# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Conservative evidence from literal client collections."""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import Final
from urllib.parse import quote, unquote

_DICTIONARIES: Final = {
    "client-dict": ("", "unproven:client"),
    "client-keys-dict": ("unproven:client", ""),
    "client-keys-values-dict": ("unproven:client", "unproven:client"),
}


def dictionary_kind(key: str, value: str) -> str:
    """Keep key and value client evidence separate, without claiming exact shapes."""
    evidence: list[str] = []
    for kind in (key, value):
        while kind.startswith("items:"):
            kind = kind[6:]
        evidence.append(
            "unproven:client"
            if kind in {"client", "unproven:client", *_DICTIONARIES}
            else ""
        )
    return next(
        (kind for kind, pair in _DICTIONARIES.items() if pair == tuple(evidence)), ""
    )


def dictionary_members(kind: str) -> tuple[str, str]:
    return _DICTIONARIES.get(kind, ("", ""))


def iterable_element(kind: str) -> str:
    if kind == "client-sequence":
        return "unproven:client"
    if kind.startswith("tuple:"):
        return "unproven:client" if "unproven:client" in tuple_members(kind) else ""
    return kind[6:] if kind.startswith("items:") else dictionary_members(kind)[0]


def tuple_kind(kinds: tuple[str, ...]) -> str:
    """Keep heterogeneous annotation positions as review evidence, not exact proofs."""
    return "tuple:" + "|".join(
        quote("unproven:client" if kind == "client" else kind, safe="")
        for kind in kinds
    )


def tuple_members(kind: str) -> tuple[str, ...]:
    return tuple(unquote(part) for part in kind.removeprefix("tuple:").split("|"))


def subscript_kind(node: ast.Subscript, infer: Callable[[ast.AST], str]) -> str:
    """Select positional tuple evidence without confusing dictionary keys and values."""
    value = infer(node.value)
    if value == "client-sequence":
        return (
            "client-sequence"
            if isinstance(node.slice, ast.Slice)
            else "unproven:client"
        )
    if value.startswith("tuple:"):
        members = tuple_members(value)
        index = node.slice
        sign = 1
        # AST predicates keep the Python 3.9 floor (no match/case).
        if isinstance(index, ast.UnaryOp) and isinstance(
            index.op, (ast.UAdd, ast.USub)
        ):
            sign = -1 if isinstance(index.op, ast.USub) else 1
            index = index.operand
        if isinstance(index, ast.Constant) and type(index.value) is int:
            position = sign * index.value
            return members[position] if -len(members) <= position < len(members) else ""
        return iterable_element(value)
    if value.startswith("dict-pair:"):
        pair = dictionary_members(value.removeprefix("dict-pair:"))
        index = node.slice
        if (
            isinstance(index, ast.Constant)
            and type(index.value) is int
            and index.value in (0, 1)
        ):
            return pair[index.value]
        return "unproven:client" if any(pair) else ""
    if any(dictionary_members(value)):
        return dictionary_members(value)[1]
    return value[6:] if value.startswith("items:") else ""


def unpacked_kinds(kind: str, size: int, star: int | None = None) -> tuple[str, ...]:
    """Unpack homogeneous sequences or the two distinct positions of dict.items()."""
    members = (
        dictionary_members(kind.removeprefix("dict-pair:"))
        if kind.startswith("dict-pair:")
        else tuple_members(kind)
        if kind.startswith("tuple:")
        else None
    )
    if members is not None:
        if star is not None and len(members) >= size - 1:
            end = len(members) - (size - star - 1)
            remainder = members[star:end]
            rest = (
                "items:" + remainder[0]
                if remainder and len(set(remainder)) == 1
                else tuple_kind(remainder)
            )
            return (*members[:star], rest, *members[end:])
        return members if len(members) == size else ("",) * size
    element = iterable_element(kind)
    return tuple(
        "items:" + element if index == star and element else element
        for index in range(size)
    )


def literal_collection_kind(node: ast.AST, infer: Callable[[ast.AST], str]) -> str:
    """Prove homogeneous sequences; dictionaries only justify manual lookup review."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return literal_collection_kind(
            ast.Dict(keys=[None, None], values=[node.left, node.right]), infer
        )
    if isinstance(node, ast.Call):
        # The resolver admits only binding-proven builtin dict calls here.
        if len(node.args) == 1 and isinstance(node.args[0], (ast.List, ast.Tuple)):
            pairs = node.args[0].elts
            if all(
                isinstance(pair, ast.Tuple) and len(pair.elts) == 2 for pair in pairs
            ):
                return literal_collection_kind(
                    ast.Dict(
                        keys=[
                            pair.elts[0]
                            for pair in pairs
                            if isinstance(pair, ast.Tuple)
                        ],
                        values=[
                            pair.elts[1]
                            for pair in pairs
                            if isinstance(pair, ast.Tuple)
                        ],
                    ),
                    infer,
                )
        if len(node.args) == 1:
            argument = node.args[0]
            if (
                isinstance(argument, ast.Call)
                and isinstance(argument.func, ast.Name)
                and argument.func.id == "zip"
            ):
                if any(
                    dictionary_kind(iterable_element(infer(value)), "")
                    for value in argument.args
                ):
                    return dictionary_kind("unproven:client", "unproven:client")
            argument_kind = infer(argument)
            element = iterable_element(argument_kind)
            if not any(dictionary_members(argument_kind)) and (
                dictionary_kind(element, "")
                or dictionary_kind(iterable_element(element), "")
                or element.startswith("dict-pair:")
                and any(dictionary_members(element.removeprefix("dict-pair:")))
            ):
                return dictionary_kind("unproven:client", "unproven:client")
        keyword_keys: list[ast.expr | None] = [
            ast.Constant(value=keyword.arg) if keyword.arg is not None else None
            for keyword in node.keywords
        ]
        return literal_collection_kind(
            ast.Dict(
                keys=[None] * len(node.args) + keyword_keys,
                values=[*node.args, *(keyword.value for keyword in node.keywords)],
            ),
            infer,
        )
    if (
        isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp))
        and len(node.generators) == 1
    ):
        generator = node.generators[0]
        if (
            isinstance(node.elt, ast.Name)
            and isinstance(generator.target, ast.Name)
            and node.elt.id == generator.target.id
        ):
            # Only forward the bound iteration value, never resolve a free name
            # against the enclosing class's (different) lexical scope.
            kind = iterable_element(infer(generator.iter))
            return "items:" + kind if kind else ""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        kinds = [infer(element) for element in node.elts]
        if kinds and kinds[0] and all(kind == kinds[0] for kind in kinds):
            return "items:" + kinds[0]
        if isinstance(node, ast.Tuple) and any(
            dictionary_kind(kind, "") for kind in kinds
        ):
            return tuple_kind(tuple(kinds))
    if isinstance(node, ast.Dict):
        kinds: list[str] = []
        keys: list[str] = []
        for key_node, value_node in zip(node.keys, node.values):
            kind = infer(value_node)
            if key_node is None:
                key, value = dictionary_members(kind)
                if kind in {"client", "unproven:client"}:
                    key = value = "unproven:client"
                keys.append(key)
                kinds.append(value)
            else:
                keys.append(infer(key_node))
                kinds.append(kind)
        key = next((kind for kind in keys if dictionary_kind(kind, "")), "")
        value = next((kind for kind in kinds if dictionary_kind("", kind)), "")
        if kind := dictionary_kind(key, value):
            return kind
        return "non-client" if all(kind == "non-client" for kind in kinds) else ""
    return ""
