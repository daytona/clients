# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Sequential import bindings, including uncertain wildcard replacement."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from ._assignment_proofs import AssignmentVisitor
from .annotation_exports import conflicting_bindings
from .call_sites import CALL_PARAMETERS
from .client_parameters import PARAMETERS
from .instance_fields import imported_class_fields
from .proof_mutations import invalidate
from .resolution import foreign_constructor, relative_module
from .source_suffixes import PACKAGE_INITIALIZERS
from .wildcard_exports import UnresolvedExports

if TYPE_CHECKING:
    from .flow import SourceContext


def import_owner(context: SourceContext, node: ast.ImportFrom) -> str:
    """Resolve relative imports against the package containing the real file."""
    current = context.module
    if context.file.replace("\\", "/").split("/")[-1] in PACKAGE_INITIALIZERS:
        current = (current + "." if current else "") + "__init__"
    return relative_module(current, node)


class ImportVisitor(AssignmentVisitor):
    """Accumulate import proofs without trusting unresolved export membership."""

    context: SourceContext
    found: bool

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[0]
            invalidate(self.env, name)
            prefix = (
                "local-module:" if self.context.index.owns(alias.name) else "module:"
            )
            self.env[name] = prefix + (
                alias.name if alias.asname else alias.name.split(".")[0]
            )
            self.found |= alias.name.split(".")[0] in self.resolver.mapping.modules
            self.env.update(
                imported_class_fields(
                    self.context.index.exports.get(alias.name, {}),
                    self.context.index.class_mutations,
                )
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = import_owner(self.context, node)
        self.found |= (
            not node.level and module.split(".")[0] in self.resolver.mapping.modules
        )
        for alias in node.names:
            if alias.name == "*":
                mapping = self.resolver.mapping
                if (
                    not node.level
                    and not self.context.index.owns(module)
                    and module
                    in (*mapping.modules, *(root + ".v0" for root in mapping.modules))
                    and mapping.v0_exports is not None
                ):
                    # The import rewrite supplies the root constructors plus
                    # v0's exports. A namespace star never supplies the
                    # hidden clients, hence preserves earlier root bindings.
                    root = module in mapping.modules
                    for name in mapping.v0_exports:
                        if not root and name in mapping.root_imports:
                            continue
                        invalidate(self.env, name)
                        self.type_bindings.pop(name, None)
                        self.env[name] = self.resolver.imported(
                            "daytona" if name in mapping.root_imports else "daytona.v0",
                            name,
                        )
                    continue
                exports = self.context.index.exports.get(module)
                declared = self.context.index.wildcard_exports.get(module)
                if exports is None:
                    self.fence("*")
                elif isinstance(declared, UnresolvedExports):
                    # Re-exported star names are present in the fixed-point index,
                    # not the module's local ScopeWrites candidate set.
                    for name in (
                        name
                        for name in declared.names | exports.keys()
                        if name.isidentifier()
                    ):
                        kind = exports.get(name, "")
                        conflict = conflicting_bindings(
                            self.resolver, (self.annotation_env(), {name: kind})
                        ).get(name)
                        invalidate(self.env, name)
                        self.type_bindings.pop(name, None)
                        # Unknown membership may both introduce and replace names.
                        # This kind supports annotations, calls and instances, but
                        # can never certify a .v0 rewrite in any downstream module.
                        known_unrelated = (
                            kind
                            in {
                                "non-client",
                                "annotation:non-client",
                                "external-instance",
                                "client-dict",
                                "client-keys-dict",
                                "client-keys-values-dict",
                            }
                            or kind.startswith(("items:", "tuple:"))
                            or (
                                kind.startswith("module:")
                                and kind[7:].split(".")[0]
                                not in self.resolver.mapping.modules
                            )
                        )
                        self.env[name] = (
                            kind.replace("items:client", "items:unproven:client")
                            if known_unrelated and not conflict
                            else "unproven:client"
                        )
                        if marker := exports.get(name + ".@mock-linked"):
                            self.env[name + ".@mock-linked"] = marker
                else:
                    for name in self.context.index.wildcard_names(module):
                        invalidate(self.env, name)
                        self.env[name] = exports[name]
                        # A star import carries mock history like a named import.
                        if marker := exports.get(name + ".@mock-linked"):
                            self.env[name + ".@mock-linked"] = marker
                continue
            name = alias.asname or alias.name
            invalidate(self.env, name)
            if marker := self.context.index.exports.get(module, {}).get(
                alias.name + ".@mock-linked"
            ):
                self.env[name + ".@mock-linked"] = marker
            imported = (
                self.context.index.exports.get(module, {}).get(alias.name, "")
                if node.level
                else self.resolver.imported(module, alias.name)
                or foreign_constructor(module, alias.name, self.context.index)
            )
            self.env[name] = imported or (
                (
                    "local-module:"
                    if node.level or self.context.index.owns(module)
                    else "module:"
                )
                + module
                + "."
                + alias.name
                if module + "." + alias.name in self.context.index.exports
                else ""
            )
            self.env[name + PARAMETERS] = self.context.index.exports.get(
                module, {}
            ).get(alias.name + PARAMETERS, "")
            self.env[name + CALL_PARAMETERS] = self.context.index.exports.get(
                module, {}
            ).get(alias.name + CALL_PARAMETERS, "")
        self.env.update(
            imported_class_fields(
                self.context.index.exports.get(module, {}),
                self.context.index.class_mutations,
            )
        )
