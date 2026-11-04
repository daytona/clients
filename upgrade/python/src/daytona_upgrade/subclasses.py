# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Carry project overrides with class proofs through aliases and import indexing."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, NamedTuple
from urllib.parse import quote

from ._member_write_flow import instance_member_writes
from .constructor_safety import constructs_config, forwards_parameters
from .field_safety import replaced_field_hooks

if TYPE_CHECKING:
    from .flow import Flow


class ClassLayer(NamedTuple):
    """One class's own members and construction trust, in a serialized C3 proof."""

    identity: str
    members: frozenset[str]
    config_safe: bool

    def encode(self) -> str:
        return "|".join(
            (
                self.identity,
                ",".join(sorted(self.members)),
                "1" if self.config_safe else "0",
            )
        )


def hierarchy(kind: str, unknown: str = "@unknown") -> list[ClassLayer]:
    """Decode internally generated class metadata without losing alias identity."""
    if kind == "type:client":
        return [ClassLayer("@sdk", frozenset(), True)]
    if not kind.startswith(
        ("type:client-subclass:", "class:", "client-subclass:", "self-subclass:")
    ):
        return [ClassLayer(unknown, frozenset({"*"}), False)]
    layers = []
    for encoded in kind.rsplit(":", 2)[-2].split(";"):
        identity, members, safe = encoded.split("|")
        layers.append(
            ClassLayer(
                identity, frozenset(filter(None, members.split(","))), safe == "1"
            )
        )
    return layers


def linearize(bases: list[list[ClassLayer]]) -> list[ClassLayer]:
    """C3 merge project-known base MROs; an inconsistent order stays unproven."""
    sequences = [list(base) for base in bases if base]
    sequences.append([base[0] for base in bases if base])
    result: list[ClassLayer] = []
    while any(sequences):
        candidate = next(
            (
                sequence[0]
                for sequence in sequences
                if sequence
                and not any(
                    sequence[0].identity == layer.identity
                    for other in sequences
                    for layer in other[1:]
                )
            ),
            None,
        )
        if candidate is None:
            return [ClassLayer("@unknown", frozenset({"*"}), False)] + [
                layer for base in bases for layer in base if layer.identity == "@sdk"
            ][:1]
        result.append(candidate)
        for sequence in sequences:
            if sequence and sequence[0].identity == candidate.identity:
                sequence.pop(0)
    return result


def constructor_kind(node: ast.ClassDef, enclosing: Flow) -> str:
    """Extend the flow's string proof vocabulary with inherited member provenance."""
    # ScopeWrites itself depends on Resolver, so load it after module initialization.
    from ._scopes import ScopeWrites

    bases = [enclosing.resolver.infer(base, enclosing.env) for base in node.bases]
    writes = ScopeWrites()
    writes.generic_visit(node)
    annotations = {
        (child.end_lineno or child.lineno, child.end_col_offset or child.col_offset)
        for child in ast.walk(node)
        if isinstance(child, ast.AnnAssign) and child.value is None
    }
    members = {
        name
        for name, position in writes.events
        if name.isidentifier() and position not in annotations
    }
    members.update(instance_member_writes(node, enclosing))
    if node.decorator_list or any(
        keyword.arg is None
        or keyword.arg == "metaclass"
        and enclosing.resolver.infer(keyword.value, enclosing.env)
        != "module:abc.ABCMeta"
        for keyword in node.keywords
    ):
        members.add("@construction-opaque")
    if replaced_field_hooks(node.name, enclosing.class_mutations):
        members.add("@fields-unsafe")
    initializer = next(
        (
            child
            for child in node.body
            if isinstance(child, ast.FunctionDef) and child.name == "__init__"
        ),
        None,
    )
    safe = not (members & {"__new__", "__init_subclass__"}) and (
        "__init__" not in members
        or initializer is not None
        and sum(name == "__init__" for name, _ in writes.events) == 1
        and forwards_parameters(initializer)
        and "super" not in enclosing.env
        and "super" not in enclosing.deferred.get(initializer, set())
    )
    if (
        initializer is not None
        and sum(name == "__init__" for name, _ in writes.events) == 1
        and not (members & {"__new__", "__init_subclass__"})
        and "super" not in enclosing.env
        and "super" not in enclosing.deferred.get(initializer, set())
        and constructs_config(initializer, enclosing)
    ):
        members.add("@config-factory")
    identity = quote(
        f"{enclosing.context.module}.{node.name}.{node.lineno}.{node.col_offset}",
        safe=".",
    )
    layers = [
        ClassLayer(identity, frozenset(members), safe),
        *linearize(
            [
                hierarchy(
                    kind,
                    quote(f"{enclosing.context.module}.{ast.dump(base)}", safe="."),
                )
                for base, kind in zip(node.bases, bases)
            ]
        ),
    ]
    members = set()
    for layer in layers:
        if layer.identity == "@sdk":
            break
        members.update(layer.members)
        if not layer.config_safe:
            members.add("@config-escape")
    client = any(
        base == "type:client" or base.startswith("type:client-subclass:")
        for base in bases
    )
    prefix = "type:client-subclass:" if client else "class:"
    return (
        prefix
        + node.name
        + ":"
        + ";".join(layer.encode() for layer in layers)
        + ":"
        + ",".join(sorted(members))
    )


def call_result(kind: str) -> str:
    """Class ancestry proves a call result only without substitution hooks."""
    if kind == "fn:literal-config":
        return "config"
    if kind.startswith("type:client-subclass:") and any(
        layer.members & {"*", "__new__", "@construction-opaque"}
        for layer in hierarchy(kind)
    ):
        return "unproven:client"
    return kind.split(":", 1)[1]


def member_receiver(kind: str, member: str) -> str:
    """Only SDK-owned members of a subclass may route to the compatibility client."""
    if kind in {"mock:client", "mock-class:client"}:
        return "client"
    if not kind.startswith(("client-subclass:", "self-subclass:")):
        return kind
    layers = hierarchy(kind)
    if any("@member-write." + member in layer.members for layer in layers):
        return "unproven:client"
    if any(layer.members & {"__getattribute__", "__getattr__"} for layer in layers):
        return "unproven:client"
    for layer in layers:
        if layer.identity == "@sdk":
            break
        if "*" in layer.members:
            return "unproven:client"
        if member in layer.members:
            return "project-member"
    return "unproven:client" if kind.startswith("self-subclass:") else "client"


def client_constructor(kind: str) -> bool:
    """Config safety applies to inherited SDK constructors too."""
    return kind == "type:client" or kind.startswith("type:client-subclass:")


def safe_config_constructor(kind: str) -> bool:
    """Only unchanged forwarding chains inherit the SDK's config-use trust."""
    return client_constructor(kind) and "@config-escape" not in kind.rsplit(":", 1)[
        -1
    ].split(",")


def owns_config_constructor(kind: str) -> bool:
    """A config factory may only delegate to unchanged SDK forwarding layers."""
    layers = hierarchy(kind)
    for index, layer in enumerate(layers):
        if "@config-factory" in layer.members:
            return all(base.config_safe for base in layers[index + 1 :])
        if not layer.config_safe:
            return False
    return False
