# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Value evidence for literal reflective attribute assignments."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .bindings import indirect_assignments
from .collection_proofs import unpacked_kinds
from .member_mutations import demote_member
from .module_proofs import module_review
from .proof_mutations import CONTAINER_WRITE, invalidate, mutates_container
from .reflective_names import canonical_name
from .resolution import Resolver, dotted
from .subclasses import client_constructor


@dataclass(frozen=True)
class AttributeProofs:
    """Share reflective value inference between local and class-wide stores."""

    resolver: Resolver
    env: dict[str, str]
    aliases: dict[str, str] | None = None

    def store_kind(self, name: str, kind: str) -> str:
        """A project setter controls the stored value, not its caller's argument."""
        root, _, member = name.rpartition(".")
        receiver = self.env.get(root, "")
        owner = (
            receiver.removeprefix("instance:")
            if receiver.startswith("instance:")
            else root
        )
        if (
            owner + ".@setter." + member in self.env
            or root + ".@setter." + member in self.env
        ):
            return (
                _review_only(kind)
                or _review_only(self.env.get(name, ""))
                or _review_only(
                    self.env.get(owner + "." + member, "").removeprefix("property:")
                )
            )
        return kind

    def bind(self, target: ast.AST, kind: str) -> None:
        """Apply a store through the shared local/class reflective inference seam."""
        if mutates_container(target, trusted_reflective=self.trusted(target)):
            invalidate(self.env, CONTAINER_WRITE)
        name = dotted(target)
        if name:
            if not kind and isinstance(target, ast.Attribute):
                # Imported holder links live in the instance payload, not env[name].
                # An opaque replacement cannot erase that review-only history.
                kind = _review_only(self.resolver.infer(target, self.env))
            demote_member(name, kind, self)
            kind = self.store_kind(name, kind)
            previous = self.env.copy()
            invalidate(self.env, name)
            self.env[name] = kind
            marker = name + ".@mock-linked"
            if (
                previous.get(marker)
                or _review_only(kind)
                or client_constructor(kind)
                or kind.startswith(("client-subclass:", "self-subclass:"))
            ):
                # History warrants mock review, never a current receiver rewrite.
                self.env[marker] = "unproven:client"
            for key, value in previous.items():
                if key != name and not self.env.get(key) and _review_only(value):
                    self.env[key] = _review_only(value)
        elif stores := self.stores(target, kind):
            for name, stored in stores:
                demote_member(name, stored, self)
                previous = self.env.copy()
                owner = name.removesuffix(".*")
                constructor = (
                    self.resolver.infer(ast.parse(owner, mode="eval").body, self.env)
                    if name.endswith(".*")
                    else ""
                )
                invalidate(self.env, name)
                self.env[name] = stored
                if constructor.startswith("class:") or client_constructor(constructor):
                    # Replacing arbitrary members cannot replace the class itself.
                    self.env[owner] = constructor
                # An unknown write cannot erase already-linked Daytona evidence.
                for key, value in previous.items():
                    if not self.env.get(key) and _review_only(value):
                        self.env[key] = _review_only(value)
                if not stored and _review_only(previous.get(name, "")):
                    self.env[name] = _review_only(previous[name])
        elif isinstance(target, (ast.Tuple, ast.List)):
            star = next(
                (
                    i
                    for i, item in enumerate(target.elts)
                    if isinstance(item, ast.Starred)
                ),
                None,
            )
            for element, element_kind in zip(
                target.elts, unpacked_kinds(kind, len(target.elts), star)
            ):
                self.bind(element, element_kind)
        elif isinstance(target, ast.Starred):
            self.bind(target.value, kind)

    def assign(self, assignment: ast.Assign, kind: str) -> None:
        """Keep exact assignment links distinct from syntactic fence candidates."""
        from .fallback_proofs import nonnull_client
        from .field_effects import CAPTURES, captures

        target = assignment.targets[0]
        name = dotted(target)
        value = assignment.value
        links: dict[str, str] = {}
        if name and kind == "client" and nonnull_client(value, self.resolver, self.env):
            links[name + ".@nonnull"] = "client"
        if name and isinstance(value, ast.Name):
            if captured := self.env.get(value.id + CAPTURES):
                links[name + CAPTURES] = captured
            links[name + ".@owner"] = self.env.get(value.id + ".@owner", value.id)
            if self.env.get(value.id + ".@mock-linked"):
                links[name + ".@mock-linked"] = "unproven:client"
        if (
            name
            and isinstance(value, ast.Dict)
            and all(
                isinstance(key, ast.Constant) and isinstance(key.value, str)
                for key in value.keys
            )
        ):
            links[name + ".@dict"] = "literal-dict:" + repr(
                sorted(
                    key.value
                    for key in value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                )
            )
            links.update(
                (name + ".@dict." + key.value, self.resolver.infer(item, self.env))
                for key, item in zip(value.keys, value.values)
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )
        self.bind(target, kind)
        self.env.update(links)
        if name and isinstance(value, ast.Lambda):
            self.env[name + CAPTURES] = captures(value)

    def stores(self, target: ast.AST, kind: str) -> tuple[tuple[str, str], ...]:
        """Infer literal values, never certify a shadowed builtin call."""
        assignments = indirect_assignments(target, self.aliases, self.env)
        if not assignments:
            return ()
        trusted = self.trusted(target)
        # An untrusted store fences containers itself; its own value still
        # carries review-only evidence, so infer it without that fence.
        env = (
            self.env
            if trusted
            else {key: item for key, item in self.env.items() if key != CONTAINER_WRITE}
        )
        stores: list[tuple[str, str]] = []
        for name, value in assignments:
            stored = self.resolver.infer(value, env) if value is not None else kind
            if not trusted and any(
                isinstance(child, ast.Call)
                and canonical_name(child.func, self.aliases or {})
                in {"setattr", "builtins.setattr", "vars", "builtins.vars"}
                for child in ast.walk(target)
            ):
                # An opaque alias need not obey setattr's literal-key contract.
                if not name.endswith(".*"):
                    stores.append((name, _review_only(stored)))
                name = name.rpartition(".")[0] + ".*"
            if name.endswith(".*"):
                stored = _review_only(stored)
            elif not trusted:
                stored = _review_only(stored)
            stores.append((name, self.store_kind(name, stored)))
            root, separator, member = name.partition(".")
            owner = self.env.get(root + ".@owner")
            if owner and separator:
                stores.append(
                    (
                        owner + "." + member,
                        self.store_kind(owner + "." + member, stored),
                    )
                )
            if separator:
                stores.extend(
                    (candidate + "." + member, _review_only(stored))
                    for key, candidate in (self.aliases or {}).items()
                    if key.startswith("@owner:" + root + ":") and candidate != owner
                )
        return tuple(stores)

    def trusted(self, target: ast.AST) -> bool:
        """Whether every reflective builtin in this store is the real builtin."""
        trusted = True
        for child in ast.walk(target):
            if isinstance(child, ast.Call):
                function = canonical_name(child.func, self.aliases or {})
                builtin = function.removeprefix("builtins.").split(".")[0]
                if builtin in {"setattr", "vars", "object"}:
                    trusted &= builtin not in self.resolver.index.builtin_writes
                if function in {"setattr", "builtins.setattr"}:
                    trusted &= (
                        self.resolver.infer(child.func, self.env)
                        == "call:builtins.setattr"
                    )
                elif function in {"vars", "object.__setattr__"}:
                    root = function.split(".")[0]
                    trusted &= (
                        dotted(child.func) == function
                        and root not in self.env
                        and root not in self.resolver.index.builtin_writes
                    )
                elif function in {"builtins.vars", "builtins.object.__setattr__"}:
                    trusted &= (
                        self.resolver.infer(ast.Name(id="builtins"), self.env)
                        == "module:builtins"
                    )
        return trusted


def _review_only(stored: str) -> str:
    """Demote exact client proofs, keeping collection evidence for review."""
    if module := module_review(stored):
        return module
    if stored in {"client", "unproven:client"}:
        return "unproven:client"
    if stored.startswith("items:") and stored.endswith(":client"):
        return stored.replace("items:client", "items:unproven:client")
    if stored == "client-sequence" or stored.startswith("tuple:"):
        return stored
    return stored if stored.endswith("dict") and "client" in stored else ""
