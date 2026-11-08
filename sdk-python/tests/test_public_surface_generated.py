# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""No public export, and no type in a public signature, comes from generated code.

The generated wire models (``daytona.v2._gen``) type the HTTP boundary only.
The public surface is hand-written: every class reachable from ``daytona`` or
``daytona.v2`` (exported, or named in a public method, property or dataclass
field of a reachable class) is walked, and each generated class it reaches is
a violation. ``ALLOWED`` lists the violations that remain; an entry that no
longer occurs fails too, so the list only shrinks. The TypeScript SDK's
tests/public-surface-generated.test.ts holds its surface to the same rule.
"""

from __future__ import annotations

import ast
import collections.abc
import dataclasses
import enum
import importlib
import inspect
import sys
import types
import typing
from typing import Callable, Iterable, Iterator, Optional

import daytona
from daytona import v2

_GENERATED = "daytona.v2._gen"
# The bundled 0.x SDK (present only in a bundled build) is not this surface.
_V0 = "daytona.v0"
# The root's v0 client types, unresolvable without the bundle.
_V0_CLIENT_NAMES = frozenset({"_DaytonaV0", "_AsyncDaytonaV0", "DaytonaV0Client", "AsyncDaytonaV0Client"})


def _is_v2_sdk(module: str) -> bool:
    return module.startswith("daytona") and not module.startswith((_GENERATED, _V0))


# Today's violations, as "<owner>.<member>: <generated class>" or
# "export <name>". Remove an entry when its type becomes SDK-owned.
ALLOWED = frozenset(
    {
        # Client, sandbox lifecycle and platform services.
        "AsyncDaytona.current_api_key: ProjectApiKeyWithContext",
        "AsyncDaytona.get_limits: OrganizationLimits",
        "AsyncDaytona.get_project_budget: ProjectBudget",
        "AsyncDaytona.list_audit_actions: AuditActionList",
        "Daytona.current_api_key: ProjectApiKeyWithContext",
        "Daytona.get_limits: OrganizationLimits",
        "Daytona.get_project_budget: ProjectBudget",
        "Daytona.list_audit_actions: AuditActionList",
        "AsyncMacModelService.catalog: MacCatalog",
        "AsyncMacModelService.list: MacModel",
        "MacModelService.catalog: MacCatalog",
        "MacModelService.list: MacModel",
        "MacReservation.__init__(capacity): MacResources",
        "MacReservation.__init__(resources_used): MacResources",
        "MacReservation.capacity: MacResources",
        "MacReservation.resources_used: MacResources",
        "AsyncSandbox.from_api(sandbox_data): Sandbox",
        "AsyncSandbox.from_api(sandbox_data): SandboxListItem",
        "AsyncSecretService.get: Secret",
        "AsyncSecretService.iter: Secret",
        "AsyncSecretService.list: Secret",
        "AsyncSecretService.set: Secret",
        "SecretService.get: Secret",
        "SecretService.iter: Secret",
        "SecretService.list: Secret",
        "SecretService.set: Secret",
        "export AuditActionGroup",
        "export AuditActionList",
        "export ImageBuildResponse",
        "export ImageListItem",
        "export MacCatalog",
        "export MacModel",
        "export OrganizationLimits",
        "export ProjectApiKeyWithContext",
        "export ProjectBudget",
        "export SandboxStateSnapshot",
        "export Secret",
        "export SecretV2",
        "export SnapshotDetail",
    }
)

# Names the generic typing machinery needs when an annotation string refers to
# them from another module (a callback alias defined elsewhere).
_TYPING_NAMES = {
    **{name: getattr(typing, name) for name in typing.__all__},
    **{name: getattr(collections.abc, name) for name in collections.abc.__all__},
}


def _namespace(module_name: str) -> dict[str, object]:
    """The module's globals plus the names it imports only for type checkers."""
    module = sys.modules.get(module_name) or importlib.import_module(module_name)
    namespace: dict[str, object] = {**_TYPING_NAMES, **vars(module)}
    try:
        tree = ast.parse(inspect.getsource(module))
    except (OSError, TypeError):
        return namespace
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
            for statement in node.body:
                try:
                    _bind_import(statement, module, namespace)
                except ImportError:
                    # The v0 bundle is absent from a source checkout.
                    continue
    return namespace


def _bind_import(statement: ast.stmt, module: types.ModuleType, namespace: dict[str, object]) -> None:
    if isinstance(statement, ast.Import):
        for alias in statement.names:
            imported = importlib.import_module(alias.name)
            namespace[alias.asname or alias.name.split(".")[0]] = (
                imported if alias.asname else importlib.import_module(alias.name.split(".")[0])
            )
    elif isinstance(statement, ast.ImportFrom):
        source = importlib.import_module("." * statement.level + (statement.module or ""), module.__package__)
        for alias in statement.names:
            value = getattr(source, alias.name, None)
            if value is None:
                value = importlib.import_module(f"{source.__name__}.{alias.name}")
            namespace[alias.asname or alias.name] = value


_NAMESPACES: dict[str, dict[str, object]] = {}


def _hints(target: Callable[..., object] | type, module_name: str) -> dict[str, object]:
    namespace = _NAMESPACES.setdefault(module_name, _namespace(module_name))
    try:
        return typing.get_type_hints(target, globalns=namespace, localns=namespace)
    except NameError as error:
        # Only the bundled v0 client types may be unresolvable (no bundle in a
        # source checkout); anything else is a real failure.
        if error.name in _V0_CLIENT_NAMES:
            return {}
        raise


def _classes(annotation: object) -> Iterator[type]:
    origin = typing.get_origin(annotation)
    if origin is not None:
        if isinstance(origin, type):
            yield origin
        for argument in typing.get_args(annotation):
            yield from _classes(argument)
    elif isinstance(annotation, type):
        yield annotation
    elif isinstance(annotation, (list, tuple)):
        for item in annotation:
            yield from _classes(item)


def _members(cls: type) -> Iterator[tuple[str, Callable[..., object], str]]:
    """Public methods and properties of ``cls``, inherited ones included.

    Yields ``(name, function, module the function's class is defined in)``;
    the nearest definition along the MRO wins, as attribute lookup does.
    """
    seen: set[str] = set()
    for owner in cls.__mro__:
        if owner is object:
            continue
        for name, value in vars(owner).items():
            if (name.startswith("_") and name != "__init__") or name in seen:
                continue
            seen.add(name)
            function: Optional[Callable[..., object]] = None
            if isinstance(value, property):
                function = value.fget
            elif isinstance(value, (staticmethod, classmethod)):
                function = value.__func__
            elif inspect.isfunction(value):
                function = value
            if function is not None:
                yield name, function, owner.__module__


def _public_exports() -> Iterator[tuple[str, object]]:
    for module, names in ((v2, v2.__all__), (daytona, daytona.__all__)):
        for name in names:
            yield name, getattr(module, name)


def _generated_on_the_public_surface(exports: Optional[Iterable[tuple[str, object]]] = None) -> set[str]:
    found: set[str] = set()
    queue: list[type] = []
    seen: set[type] = set()

    def reach(position: str, annotation: object) -> None:
        for cls in _classes(annotation):
            module = getattr(cls, "__module__", "") or ""
            if module.startswith(_GENERATED):
                found.add(f"{position}: {cls.__name__}")
            elif _is_v2_sdk(module) and cls not in seen:
                queue.append(cls)

    def visit(owner: str, member: str, annotation: object) -> None:
        reach(f"{owner}.{member}", annotation)

    for name, value in _public_exports() if exports is None else exports:
        if isinstance(value, types.ModuleType):
            continue
        value_module = getattr(value, "__module__", "") or ""
        if value_module.startswith(_GENERATED):
            found.add(f"export {name}")
        elif isinstance(value, type):
            if _is_v2_sdk(value_module):
                queue.append(value)
        elif inspect.isfunction(value):
            # An exported function: its parameters and return type.
            for parameter, annotation in _hints(value, value_module).items():
                reach(f"{name}()" if parameter == "return" else f"{name}({parameter})", annotation)
        else:
            # Any other exported value (a typing alias, ...) by what it names.
            reach(f"export {name}", value)

    while queue:
        cls = queue.pop()
        if cls in seen:
            continue
        seen.add(cls)
        if issubclass(cls, (BaseException, enum.Enum)):
            continue
        if dataclasses.is_dataclass(cls):
            field_hints = _hints(cls, cls.__module__)
            for field in dataclasses.fields(cls):
                if not field.name.startswith("_") and field.name in field_hints:
                    visit(cls.__name__, field.name, field_hints[field.name])
        for member, function, module in _members(cls):
            for parameter, annotation in _hints(function, module).items():
                visit(cls.__name__, member if parameter == "return" else f"{member}({parameter})", annotation)
    return found


def test_no_public_export_or_signature_type_comes_from_generated_code() -> None:
    found = _generated_on_the_public_surface()
    assert sorted(found - ALLOWED) == [], "generated types on the public surface (use an SDK-owned model)"
    assert sorted(ALLOWED - found) == [], "stale ALLOWED entries: remove them"


def test_the_guard_walks_exported_functions_and_aliases() -> None:
    from daytona.v2._gen.api.models.sandbox import Sandbox as WireSandbox

    def probe_public_function(sandbox: object) -> object:
        return sandbox

    # Evaluated annotations: this module's own are strings (postponed).
    probe_public_function.__annotations__ = {"sandbox": WireSandbox, "return": Optional[WireSandbox]}
    probe_public_function.__module__ = __name__
    found = _generated_on_the_public_surface(
        [
            ("probe_public_function", probe_public_function),
            ("ProbeAlias", Optional[WireSandbox]),
            ("PROBE_CONSTANT", "a string"),
        ]
    )
    assert found == {
        "probe_public_function(sandbox): Sandbox",
        "probe_public_function(): Sandbox",
        "export ProbeAlias: Sandbox",
    }
