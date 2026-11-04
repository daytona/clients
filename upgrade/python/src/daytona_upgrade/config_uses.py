# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Lexical ownership and non-escaping observations of real config objects."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Final

from ._scopes import ScopeWrites
from .cloud_api_url import cloud_url_identity
from .config_members import FACTORIES, MEMBERS
from .subclasses import client_constructor, safe_config_constructor

if TYPE_CHECKING:
    from .config_origins import ConfigOrigins


READERS: Final = frozenset(
    {
        "call:builtins.print",
        "call:builtins.repr",
        "call:builtins.str",
        "call:json.dumps",
    }
)
LOG_METHODS: Final = frozenset(
    {
        "debug",
        "info",
        "warning",
        "warn",
        "error",
        "exception",
        "critical",
        "fatal",
        "log",
    }
)


class ConfigUses:
    """Cache lexical bindings and classify config references, not their spelling."""

    def __init__(self, origins: ConfigOrigins) -> None:
        self.origins = origins
        self.hooks = origins.flow.context.index.config_hooks or any(
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and node.attr.startswith("__")
            and node.attr.endswith("__")
            and origins.kind(node.value) == "type:config"
            for node in origins.parents
        )
        self.bindings: dict[ast.AST, set[str]] = {}
        self.shared: dict[ast.AST, set[str]] = {}
        for scope in origins.scopes:
            writes = ScopeWrites()
            writes.generic_visit(scope)
            names = {name for name, _ in writes.events if "." not in name}
            if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                names.update(
                    arg.arg
                    for arg in [
                        *scope.args.posonlyargs,
                        *scope.args.args,
                        *scope.args.kwonlyargs,
                    ]
                )
                names.update(
                    arg.arg for arg in [scope.args.vararg, scope.args.kwarg] if arg
                )
            self.bindings[scope] = names
            self.shared[scope] = writes.shared

    def owner(self, node: ast.Name) -> ast.AST | None:
        """Find the owning scope, skipping class locals for deferred function bodies."""
        scope = self.origins.scope(node)
        skip_classes = False
        while True:
            if not (skip_classes and isinstance(scope, ast.ClassDef)):
                if node.id in self.shared[scope] and any(
                    isinstance(child, ast.Global) and node.id in child.names
                    for child in self.origins.scopes[scope]
                ):
                    return self.origins.module
                if (
                    node.id not in self.shared[scope]
                    and node.id in self.bindings[scope]
                ):
                    return scope
            if scope is self.origins.module:
                return None
            skip_classes |= isinstance(
                scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            )
            scope = self.origins.scope(scope)

    def default(self, value: ast.expr | None) -> bool:
        """Match the constructor's literal default/Cloud URL policy."""
        return isinstance(value, ast.Constant) and (
            value.value is None
            or isinstance(value.value, str)
            and any(
                cloud_url_identity(value.value) == cloud_url_identity(url)
                for url in self.origins.flow.resolver.mapping.cloud_urls
            )
        )

    def trusted(self, function: ast.expr) -> bool:
        """A forward user definition must not inherit a built-in reader's trust."""
        binding = self.origins.kind(function)
        if binding.removeprefix("call:builtins.") in (
            self.origins.flow.context.index.builtin_writes
        ):
            return False
        if not isinstance(function, ast.Name) or not self.origins.kind(
            function
        ).startswith("call:builtins."):
            return True
        owner = self.owner(function)
        return owner is None or not any(
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and child.name == function.id
            for child in self.origins.scopes[owner]
        )

    def safe(self, node: ast.expr) -> bool:
        """Recognize observations and removable writes; unknown uses escape."""
        parent = self.origins.parents[node]
        # isinstance is required here: the tool supports Python 3.9, before match.
        if isinstance(parent, ast.Attribute):
            if isinstance(parent.ctx, ast.Load):
                if parent.attr in FACTORIES:
                    call = self.origins.parents[parent]
                    # Only direct calls reach factory payload analysis. A detached
                    # bound method can conceal arbitrary updates at a later use.
                    return (
                        not self.hooks
                        and isinstance(call, ast.Call)
                        and call.func is parent
                    )
                # Known scalar fields cannot expose a config alias or bound mutator.
                return not self.hooks and parent.attr in (
                    self.origins.flow.resolver.mapping.config.keys() | MEMBERS
                )
            statement = self.origins.parents[parent]
            return (
                isinstance(parent.ctx, ast.Store)
                and parent.attr in {"api_url", "server_url"}
                and isinstance(statement, (ast.Assign, ast.AnnAssign))
                and self.default(statement.value)
            )
        if isinstance(parent, ast.Expr):
            return True
        if isinstance(parent, ast.FormattedValue):
            return not self.hooks
        if isinstance(parent, ast.keyword):
            parent = self.origins.parents[parent]
        if not isinstance(parent, ast.Call) or parent.func is node:
            return False
        binding = self.origins.kind(parent.func)
        if binding == "call:json.dumps" and any(
            keyword.arg in {"default", "cls", None}
            and not (
                isinstance(keyword.value, ast.Constant)
                and keyword.value.value is None
                or self.trusted(keyword.value)
                and self.origins.kind(keyword.value)
                in {"call:builtins.str", "call:builtins.repr"}
            )
            for keyword in parent.keywords
        ):
            # A custom encoder receives the original object, not its text.
            return False
        if not self.trusted(parent.func):
            return False
        if client_constructor(binding):
            return safe_config_constructor(binding)
        if binding in READERS:
            return not self.hooks
        if binding.startswith(("module:logging.", "call:logging.")):
            return not self.hooks and binding.rsplit(".", 1)[1] in LOG_METHODS
        return (
            binding == "call:builtins.setattr"
            and len(parent.args) == 3
            and not parent.keywords
            and parent.args[0] is node
            and isinstance(parent.args[1], ast.Constant)
            and parent.args[1].value in {"api_url", "server_url"}
            and self.default(parent.args[2])
        )

    def client_argument(self, node: ast.expr) -> bool:
        """Only direct client config arguments permit destructive URL cleanup."""
        parent = self.origins.parents[node]
        if isinstance(parent, ast.keyword):
            if parent.arg != "config":
                return False
            parent = self.origins.parents[parent]
        return (
            isinstance(parent, ast.Call)
            and parent.func is not node
            and client_constructor(self.origins.kind(parent.func))
            and safe_config_constructor(self.origins.kind(parent.func))
        )
