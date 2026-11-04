# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Binding-proven mock specs certify only bounded client mock shapes."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from .expression_names import dotted
from .mock_configuration import configuration_collision
from .mock_spec_origins import MockSpecOrigins
from .subclasses import client_constructor

if TYPE_CHECKING:
    from .resolution import Resolver

SPEC_REVIEWS: Final = {
    "mock:async-client": "Mock spec references AsyncDaytona; in SDK 2.x .v0 is a property, so configure it with its own async-spec'd mock before moving calls to .v0.",
    "mock:uncertain": "Mock spec references a Daytona class but its shape is unresolved; configure the v0 client mock separately.",
}


@dataclass(frozen=True)
class MockInference:
    """Read mock evidence from the same evaluation environment as the resolver."""

    resolver: Resolver
    env: dict[str, str]

    def review(self, node: ast.AST) -> str:
        """Keep mock review even when walrus writes forbid ordinary proofs."""
        if isinstance(node, ast.Call) and any(
            isinstance(child, ast.NamedExpr) for child in ast.walk(node)
        ):
            kind = self.call(node, self.resolver.infer(node.func, self.env))
            if kind in {"mock:async-client", "mock:uncertain"}:
                return kind
        return ""

    def configure(self, node: ast.Call) -> None:
        """Record result-shape mutations, including possible constructor aliases."""
        from .proof_mutations import invalidate

        if not (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "configure_mock"
            and self.resolver.infer(node.func.value, self.env)
            in {"mock:client", "mock-class:client"}
        ):
            return
        keys: list[str | None] = []
        for keyword in node.keywords:
            if keyword.arg is not None:
                keys.append(keyword.arg)
            elif isinstance(keyword.value, ast.Dict):
                keys.extend(
                    key.value
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    else None
                    for key in keyword.value.keys
                )
            else:
                keys.append(None)
        members = set(keys)
        if self.resolver.infer(node.func.value, self.env) == "mock:client":
            if (
                node.args
                or None in members
                or configuration_collision(
                    (key for key in keys if key is not None),
                    self.resolver.mapping.moved,
                )
            ):
                # Instance identities are not tracked: fence potential aliases too.
                owners = (
                    {dotted(node.func.value)}
                    | {name for name, kind in self.env.items() if kind == "mock:client"}
                    | {
                        name + ".return_value"
                        for name, kind in self.env.items()
                        if kind == "mock-class:client"
                    }
                )
                for owner in owners:
                    if not owner:
                        continue
                    invalidate(self.env, owner)
                    self.env[owner] = "mock:uncertain"
            return
        changed = {"return_value", "side_effect"}
        if None not in members:
            changed.intersection_update(members)
        owners = {dotted(node.func.value)} | {
            name for name, kind in self.env.items() if kind == "mock-class:client"
        }
        for owner in owners:
            for member in changed:
                name = owner + "." + member
                invalidate(self.env, name)
                self.env[name] = "mock:uncertain"

    def member(self, node: ast.AST) -> str:
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "return_value"
            and self.resolver.infer(node.value, self.env) == "mock-class:client"
        ):
            # Explicit stores (including empty invalidation entries) belong to
            # the ordinary resolver, not the autospec's original default shape.
            if dotted(node) in self.env or dotted(node.value) + ".*" in self.env:
                return ""
            return (
                "mock:uncertain"
                if self.changed("return_value", dotted(node.value))
                else "mock:client"
            )
        return ""

    def changed(self, member: str, receiver: str) -> bool:
        """A same-kind mock can alias this constructor; never resurrect its default."""
        return any(
            attribute in {member, "*"}
            and (owner == receiver or self.env.get(owner) == "mock-class:client")
            for name in self.env
            for owner, _, attribute in (name.rpartition("."),)
        )

    def call(self, node: ast.Call, function: str) -> str:
        """Preserve spec evidence without re-resolving the already-proven callee."""
        if function == "mock-class:client":
            if self.changed("side_effect", dotted(node.func)):
                return "mock:uncertain"
            value = dotted(node.func) + ".return_value"
            if value in self.env:
                return self.env[value] or "mock:uncertain"
            return (
                "mock:uncertain"
                if self.changed("return_value", dotted(node.func))
                else "mock:client"
            )
        if (
            (
                dotted(node.func) == "type"
                and "type" not in self.env
                or function in {"module:builtins.type", "call:builtins.type"}
            )
            and "type" not in self.resolver.index.builtin_writes
            and len(node.args) == 1
            and not node.keywords
        ):
            instance = self.resolver.infer(node.args[0], self.env)
            if instance == "client" or instance.startswith("client-subclass:"):
                return "type:" + instance
        path = function.removeprefix("module:").removeprefix("call:")
        module, _, helper = path.rpartition(".")
        if helper in {"object", "multiple"} and module.endswith(".patch"):
            module, helper = module.removesuffix(".patch"), "patch." + helper
        if module not in {"unittest.mock", "mock", "pytest_mock.MockerFixture"}:
            return ""
        patch = helper in {"patch", "patch.object", "patch.multiple"}
        if not patch and helper not in {
            "Mock",
            "MagicMock",
            "create_autospec",
            "AsyncMock",
            "NonCallableMock",
            "NonCallableMagicMock",
        }:
            return ""
        keywords = {keyword.arg: keyword.value for keyword in node.keywords}
        specs = [
            value
            for name, value in keywords.items()
            if name in {"spec", "spec_set", "autospec"}
        ]
        if node.args and not patch:
            specs.append(node.args[0])
        if any(
            MockSpecOrigins(self.resolver, self.env).asynchronous(spec)
            for spec in specs
        ):
            return "mock:async-client"
        if not any(
            client_constructor(self.resolver.infer(spec, self.env)) for spec in specs
        ):
            return (
                "mock:uncertain"
                if any(
                    MockSpecOrigins(self.resolver, self.env).origin(
                        spec.func if isinstance(spec, ast.Call) else spec,
                        ".@mock-linked",
                    )
                    for spec in specs
                )
                else ""
            )
        if patch or helper not in {"Mock", "MagicMock", "create_autospec"}:
            return "mock:uncertain"
        autospec = helper == "create_autospec"
        spec = keywords.get("spec", node.args[0] if node.args else None)
        if not autospec and "spec_set" in keywords:
            spec = keywords["spec_set"]
        allowed = {"spec", "spec_set", "name", "unsafe"}
        if autospec:
            allowed.add("instance")
        if (
            len(node.args) > 1
            or any(isinstance(arg, ast.Starred) for arg in node.args)
            or any(name not in allowed for name in keywords)
            or spec is None
            or self.resolver.infer(spec, self.env) != "type:client"
            or not MockSpecOrigins(self.resolver, self.env).synchronous(spec)
            or node.args
            and "spec" in keywords
            or autospec
            and any(
                not (isinstance(value, ast.Constant) and isinstance(value.value, bool))
                for name, value in keywords.items()
                if name in {"instance", "spec_set"}
            )
        ):
            return "mock:uncertain"
        instance = keywords.get("instance")
        if autospec and not (
            isinstance(instance, ast.Constant) and instance.value is True
        ):
            return "mock-class:client"
        return "mock:client"
