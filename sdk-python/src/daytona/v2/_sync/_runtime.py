# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Hand-written glue beneath the generated ``Sync*`` twins.

Everything in this package except this file is emitted by
``gen/gen_sync.py``. This module is the only place with logic: turning the
async implementation's return values into their ``Sync*`` twins (and back
for arguments), and the sentinel bridge a rehydrated, not-yet-connected
handle carries.
"""

from __future__ import annotations

import dataclasses
from typing import Iterator, TypeVar

from .._bridge import SyncBridge
from ..errors import create_error

_T = TypeVar("_T")

# async class -> Sync twin. Populated by the generated package __init__ once
# every twin module has been imported; empty until then by construction.
REGISTRY: dict[type, type] = {}


def wrap(value: object, bridge: SyncBridge) -> object:
    """Substitute async SDK handles (and containers of them) with Sync twins.

    Erased on purpose: the generated call sites carry the precise Sync return
    annotation; the registry walk beneath them is structural.
    """
    twin = REGISTRY.get(type(value))
    if twin is not None:
        return twin(value, bridge)
    if isinstance(value, list):
        return [wrap(item, bridge) for item in value]
    if isinstance(value, tuple):
        return tuple(wrap(item, bridge) for item in value)
    if isinstance(value, dict):
        return {key: wrap(item, bridge) for key, item in value.items()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type) and hasattr(value, "items"):
        return dataclasses.replace(value, items=wrap(value.items, bridge))  # type: ignore[type-var]
    return value


def wrap_list(items: list[object], bridge: SyncBridge, twin: type[_T]) -> list[_T]:
    return [twin(item, bridge) for item in items]  # type: ignore[call-arg]


def wrap_iter(items: Iterator[object], bridge: SyncBridge, twin: type[_T]) -> Iterator[_T]:
    for item in items:
        yield twin(item, bridge)  # type: ignore[call-arg]


def wrap_page(page: object, bridge: SyncBridge, twin: type[_T]) -> object:
    # dataclasses.replace keeps every other field (total, total_relation,
    # summary fields) exactly as the async call returned them.
    return dataclasses.replace(page, items=wrap_list(page.items, bridge, twin))  # type: ignore[attr-defined,type-var]


def unwrap(value: object) -> object:
    """Inverse of :func:`wrap`: Sync handles (or containers of them) passed
    back into the SDK become the async objects the implementation expects."""
    impl = getattr(value, "_impl", None)
    if impl is not None and REGISTRY.get(type(impl)) is type(value):
        return impl
    if isinstance(value, list):
        return [unwrap(item) for item in value]
    if isinstance(value, tuple):
        return tuple(unwrap(item) for item in value)
    if isinstance(value, set):
        return {unwrap(item) for item in value}
    if isinstance(value, dict):
        return {key: unwrap(item) for key, item in value.items()}
    return value


class UnboundBridge(SyncBridge):
    """Stands in for the bridge on a rehydrated, not-yet-connected handle.

    Such a handle has no transport either, so the async implementation would
    raise "not bound" on any I/O method; this raises that same class of error
    one step earlier instead of starting an event loop for nothing.
    """

    def __init__(self) -> None:  # no loop thread, no fork/atexit registration
        self._closed = False
        self._name = "an unbound handle"

    def call(self, operation: object) -> object:  # type: ignore[override]
        raise create_error(
            "handle is not bound to a client; connect it through "
            "`sandbox.process.connect(handle.to_json())` (or the matching namespace) first",
            400,
            "BAD_REQUEST",
            "SDK",
        )

    call_sync = call  # type: ignore[assignment]

    def on_loop_thread(self) -> bool:
        return False

    def iterate(self, agen: object) -> Iterator[object]:  # type: ignore[override]
        self.call(agen)
        return iter(())

    def close(self, finalizer: object = None) -> None:
        return None


UNBOUND = UnboundBridge()
