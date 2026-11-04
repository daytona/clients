# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Sequential import bindings for the class-wide field-write collector."""

from __future__ import annotations

import ast

from .compoundflow import CompoundVisitor
from .proof_mutations import invalidate


class ClassImportVisitor(CompoundVisitor):
    """Imports establish identities; joins and later writes still fence those facts."""

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[0]
            invalidate(self.env, name)
            prefix = (
                "local-module:" if self.resolver.index.owns(alias.name) else "module:"
            )
            self.env[name] = prefix + (alias.name if alias.asname else name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            name = alias.asname or alias.name
            if name == "*":
                self.fence("*")
                continue
            invalidate(self.env, name)
            self.env[name] = (
                self.resolver.imported(node.module, alias.name)
                or (
                    (
                        "local-module:"
                        if self.resolver.index.owns(node.module)
                        else "module:"
                    )
                    + node.module
                    + "."
                    + alias.name
                    if node.module + "." + alias.name in self.resolver.index.exports
                    else ""
                )
                if node.module and not node.level
                else ""
            )
