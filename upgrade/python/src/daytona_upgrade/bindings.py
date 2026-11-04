# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Invalidation shared by lexical, control-flow and class-wide proof analysis."""

from __future__ import annotations

import ast

from .reflective_names import canonical_name
from .resolution import dotted


def dictionary_owner(node: ast.AST, aliases: dict[str, str] | None = None) -> str:
    """Name an instance exposed through __dict__ or vars on the Python 3.9 AST."""
    if isinstance(node, ast.Attribute) and node.attr == "__dict__":
        return dotted(node.value)
    if (
        isinstance(node, ast.Call)
        and canonical_name(node.func, aliases or {}) in {"vars", "builtins.vars"}
        and len(node.args) == 1
    ):
        return dotted(node.args[0])
    return ""


def indirect_writes(
    node: ast.AST, aliases: dict[str, str] | None = None
) -> tuple[str, ...]:
    """Name fields replaced through instance dictionaries or setattr."""
    names = tuple(name for name, _ in indirect_assignments(node, aliases))
    calls = [child for child in ast.walk(node) if isinstance(child, ast.Call)]
    if names and any(
        canonical_name(call.func, aliases or {}) != dotted(call.func) for call in calls
    ):
        # A syntactic alias may be rebound before execution. Summaries fence the
        # whole object; sequential inference alone may trust its literal key.
        return (*names, *(name.rpartition(".")[0] + ".*" for name in names))
    return names


def indirect_assignments(
    node: ast.AST,
    aliases: dict[str, str] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[tuple[str, ast.AST | None], ...]:
    """Pair reflective destinations with values; unknown keys fence the owner."""
    key: ast.AST
    value: ast.AST | None = None
    if isinstance(node, ast.Subscript):
        name = dictionary_owner(node.value, aliases)
        key = node.slice
    elif (
        isinstance(node, ast.Call)
        and canonical_name(node.func, aliases or {})
        in {
            "setattr",
            "builtins.setattr",
            "object.__setattr__",
            "builtins.object.__setattr__",
        }
        and len(node.args) == 3
    ):
        owner, key, value = node.args
        name = dotted(owner)
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "update"
        and (name := dictionary_owner(node.func.value, aliases))
    ):
        members: list[tuple[str | None, ast.AST | None]] = []
        for argument in node.args:
            prefix = dotted(argument) + ".@dict."
            literal = [
                (key.removeprefix(prefix), ast.Name(id=key))
                for key in env or {}
                if key.startswith(prefix)
                and (env or {})
                .get(dotted(argument) + ".@dict", "")
                .startswith("literal-dict:")
            ]
            if literal:
                members.extend(literal)
                continue
            if not isinstance(argument, ast.Dict):
                return ((name + ".*", argument),)
            members.extend(
                (
                    key.value
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    else None,
                    value,
                )
                for key, value in zip(argument.keys, argument.values)
            )
        members.extend((keyword.arg, keyword.value) for keyword in node.keywords)
        if any(member is None or not member.isidentifier() for member, _ in members):
            return ((name + ".*", None),)
        # Later update entries win, just as in Python's dictionary construction.
        return tuple(
            {
                name + "." + member: value
                for member, value in members
                if member is not None
            }.items()
        )
    else:
        return ()
    member = (
        key.value
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
        else "*"
    )
    # Reflective keys need not be attribute syntax. Fence the actual owner
    # rather than inventing a dotted path that may not even parse.
    if member == "__dict__" or not member.isidentifier():
        member = "*"
    return ((name + "." + member, value),) if name else ()


def invalidate(env: dict[str, str], name: str) -> None:
    """Mutate the proof environment when a base object is replaced or shadowed."""
    # A wildcard invalidates every descendant of its actual receiver, including
    # nested receivers such as holder.cache, not just top-level roots.
    affected = {name.removesuffix(".*") if name.endswith(".*") else name}
    root, separator, member = name.partition(".")
    if separator and not (name.endswith(".*") and "." in name[:-2]):
        # Receiver kinds prove a type, not object identity: equally typed roots
        # (or roots with no known type) can alias the object being mutated.
        kind = env.get(root, "")
        affected.update(
            candidate if member == "*" else candidate.split(".", 1)[0] + "." + member
            for candidate in env
            if "." in candidate
            and (
                member == "*"
                or candidate.partition(".")[2] == member
                or candidate.partition(".")[2].startswith(member + ".")
            )
            and (
                not kind
                or not env.get(candidate.split(".", 1)[0], "")
                or env.get(candidate.split(".", 1)[0]) == kind
            )
        )
    for key in list(env):
        if any(key.startswith(target + ".") for target in affected) or (
            key.endswith(".@owner") and not name.endswith(".*") and env[key] == name
        ):
            del env[key]
    for target in affected:
        env[target] = ""
