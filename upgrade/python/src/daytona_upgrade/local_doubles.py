# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Whole-scope ownership proofs for local module fakes and client doubles."""

from __future__ import annotations

import ast

from .client_double_targets import ClientDoubleTargets
from .double_ownership import DoubleScope, in_one_line_suite, scope_nodes
from .factory_patch_reviews import factory_patch_sites
from .flow import Flow
from .local_class_doubles import class_factories
from .models import Item
from .module_proofs import V0_SUBMODULES


def prove_local_doubles(flow: Flow) -> list[Item]:
    """Refine source-position proofs only; never export mock identities or shapes."""
    targets = ClientDoubleTargets(flow)
    parents = {
        child: parent
        for parent in ast.walk(flow.module)
        for child in ast.iter_child_nodes(parent)
    }
    clients = targets.clients(safe_only=True)
    classes = class_factories(targets)
    accepted: set[ast.expr] = set()
    class_uses: dict[ast.Call, set[ast.expr]] = {}
    conflicts: set[ast.expr] = set()
    bound_names = {
        target.id
        for node in ast.walk(flow.module)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }
    for scope in ast.walk(flow.module):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nodes = [child for statement in scope.body for child in scope_nodes(statement)]
        installations = {
            replacement: node
            for node in nodes
            if isinstance(node, ast.Call)
            and (replacement := targets.replacement(node)) is not None
        }
        for node in nodes:
            if not (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                continue
            name = node.targets[0].id
            module_uses = {use for use in installations if use.id == name}
            module = bool(module_uses)
            cls = classes.get(node.value) if isinstance(node.value, ast.Call) else None
            if not targets.factory(node.value, module) and cls is None:
                continue
            allowed: set[ast.expr] = set(module_uses) if module else clients.copy()
            aliases = [
                child
                for child in nodes
                if isinstance(child, ast.Assign)
                and len(child.targets) == 1
                and isinstance(child.targets[0], ast.Attribute)
                and child.targets[0].attr == "v0"
                and isinstance(child.targets[0].value, ast.Name)
                and child.targets[0].value.id == name
                and isinstance(child.value, ast.Name)
                and child.value.id == name
            ]
            namespace = (
                isinstance(node.value, ast.Call)
                and targets.kind(node.value.func) == "module:types.SimpleNamespace"
            )
            ready = (
                any(
                    alias.lineno == (node.end_lineno or node.lineno) + 1
                    and parents.get(alias) is parents.get(node)
                    for alias in aliases
                )
                if module
                else namespace
                and isinstance(node.value, ast.Call)
                and any(kw.arg == "v0" for kw in node.value.keywords)
            )
            if module:
                allowed.update(alias.value for alias in aliases)
            attributes = DoubleScope(scope, parents).references(node, allowed)
            if attributes is None:
                continue
            members = (
                (set(V0_SUBMODULES) | set(flow.resolver.mapping.root_imports) | {"v2"})
                if module
                else set(flow.resolver.mapping.moved) | {"v2", "close"}
            )
            if ready:
                members.add("v0")
            accessed = {
                member.attr
                for reference in attributes
                if isinstance(member := parents.get(reference), ast.Attribute)
            }
            if (
                module
                and "v0" in accessed
                and not accessed.intersection(V0_SUBMODULES)
                and isinstance(node.value, ast.Call)
                and targets.kind(node.value.func)
                in {
                    f"module:{module_}.{name_}"
                    for module_ in ("mock", "unittest.mock")
                    for name_ in ("Mock", "MagicMock")
                }
            ):
                ready = True
                members.add("v0")
            if not module and cls is None and "v0" in accessed:
                if accessed.intersection(flow.resolver.mapping.moved):
                    conflicts.update(
                        use
                        for use in clients
                        if isinstance(use, ast.Name)
                        and use.id == name
                        and use in set(ast.walk(scope))
                    )
                    for reference in attributes:
                        targets.mark(reference, "unproven:client")
                    continue
                members.add("v0")
                ready = True
            if any(
                isinstance(member := parents.get(reference), ast.Attribute)
                and (
                    member.attr == "v0"
                    and not ready
                    or cls is None
                    and member.attr not in members
                )
                for reference in attributes
            ):
                continue
            references: set[ast.expr] = {
                use
                for use in clients
                if isinstance(use, ast.Name)
                and use.id == name
                and use in set(ast.walk(scope))
            }
            if cls is not None and isinstance(node.value, ast.Call):
                class_uses[node.value] = references | {node.value}
                continue
            if namespace and not module and not ready:
                targets.mark(node.value, "local-double:namespace")
            if (
                module
                and not ready
                and isinstance(node.value, ast.Call)
                and targets.kind(node.value.func)
                in {"module:types.SimpleNamespace", "module:types.ModuleType"}
            ):
                if scope.lineno == node.lineno or in_one_line_suite(node, parents):
                    # No statement line to append `fake.v0 = fake` to: leave the fake and its
                    # patch site unmarked, so the patch gets the mock-target review item.
                    continue
                targets.mark(node, "local-double:module-alias:" + name)
            accepted.update(references | {node.value})
            for reference in attributes:
                targets.mark(
                    reference,
                    "local-double:module" if module else "local-double:client",
                )
                member = parents.get(reference)
                while isinstance(member, ast.Attribute):
                    targets.mark(member, "local-double:member")
                    member = parents.get(member)
            for reference in module_uses:
                targets.mark(installations[reference], "local-double:module-patch")
    for node in clients:
        if node in accepted or not isinstance(node, ast.Call):
            continue
        if node in classes:
            class_uses.setdefault(node, {node})
        elif targets.kind(
            node.func
        ) == "module:types.SimpleNamespace" and targets.factory(node):
            # A literal in the certified slot has no external aliases or member setup.
            if not any(kw.arg == "v0" for kw in node.keywords):
                targets.mark(node, "local-double:namespace")
            accepted.add(node)
    for cls in set(classes.values()):
        calls = {call for call, owner in classes.items() if owner is cls}
        if calls <= class_uses.keys():
            if not any(
                isinstance(method, ast.FunctionDef) and method.name == "v0"
                for method in cls.body
            ):
                targets.mark(cls, "local-double:class")
            for call in calls:
                accepted.update(class_uses[call])
    pending = {
        node
        for node in targets.clients() - accepted
        if not isinstance(node, ast.Name)
        or node not in flow.client_arguments
        or node.id in bound_names
        if (
            node in conflicts
            or targets.kind(node)
            not in {"client", "client.v0", "mock:client", "mock:async-client"}
        )
        and not (isinstance(node, ast.Constant) and node.value is None)
        and not _memberless(node, targets)
    }
    sites = factory_patch_sites(targets, accepted)
    return [
        {
            "file": flow.context.file,
            "line": node.lineno,
            "column": node.col_offset + 1,
            "kind": "mock-target",
            "message": "Mock replacement reaches a Daytona client consumer but its v0 shape is unresolved; give the replacement a v0 attribute before using SDK 2.x.",
        }
        for node in sorted(
            {sites.get(node, node) for node in pending} | set(sites.values()),
            key=lambda node: (node.lineno, node.col_offset),
        )
    ]


def _memberless(node: ast.AST, targets: ClientDoubleTargets) -> bool:
    """`object()` / bare `SimpleNamespace()` placeholders have no client members to move.

    Any moved-member access on them already failed under 0.x, so migration cannot break them.
    """
    while (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "cast"
        and len(node.args) == 2
    ):
        node = node.args[1]
    return (
        isinstance(node, ast.Call)
        and not node.args
        and not node.keywords
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "object")
            or targets.kind(node.func) == "module:types.SimpleNamespace"
        )
    )
