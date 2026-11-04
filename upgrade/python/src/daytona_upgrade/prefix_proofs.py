# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Memoize unchanged prefix joins without caching mutable binding environments."""

from __future__ import annotations

from .annotation_exports import conflicting_bindings, joined_bindings
from .resolution import Resolver


class PrefixProofs:
    """Mutable observations are bounded by the currently active lexical prefixes."""

    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver
        self.observations: dict[int, tuple[dict[str, str], dict[str, str]]] = {}

    def observe(
        self, prefixes: tuple[dict[str, str], ...], env: dict[str, str]
    ) -> None:
        """A repeated AST visit with identical inputs cannot change the join."""
        active = {id(proofs) for proofs in prefixes}
        self.observations = {
            key: state for key, state in self.observations.items() if key in active
        }
        for proofs in prefixes:
            previous = self.observations.get(id(proofs))
            if previous is not None and previous[0] == proofs and previous[1] == env:
                continue
            if previous is not None and previous[0] == proofs:
                changed = {
                    name
                    for name in previous[1].keys() | env.keys()
                    if previous[1].get(name) != env.get(name)
                }
                # Alias evidence can change when a referenced name changes, even
                # if the alias itself does not. Always re-evaluate these kinds.
                changed.update(
                    name
                    for branch in (proofs, env)
                    for name, kind in branch.items()
                    if kind.startswith(("type:", "annotation:", "alias:"))
                )
                merged = {
                    name: kind
                    for name, kind in proofs.items()
                    if name not in changed or kind == env.get(name)
                }
                merged.update(
                    conflicting_bindings(self.resolver, (proofs, env), changed)
                )
            else:
                merged = joined_bindings(self.resolver, (proofs, env))
            proofs.clear()
            proofs.update(merged)
            self.observations[id(proofs)] = (proofs.copy(), env.copy())
