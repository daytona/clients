# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The committed surface snapshots (gen/api_surface.py) cover what users reach
without importing it by name: the v2 client behind the root's ``.v2``, the
types public signatures use without exporting them, and the constructors of
the option dataclasses users build."""

from __future__ import annotations

import typing
from pathlib import Path

import pytest

from daytona import AsyncDaytona, Daytona, DaytonaConfig
from daytona.v2 import types as v2_types

ROOT = Path(__file__).resolve().parents[1]


def snapshot(name: str) -> list[str]:
    return (ROOT / "api-surface" / name).read_text().splitlines()


def key_of(cls: type) -> str:
    return f"~{cls.__name__}@{cls.__module__.replace('.', '/')}"


@pytest.mark.parametrize("root", [Daytona, AsyncDaytona])
def test_every_public_member_of_the_v2_client_is_listed(root: type[Daytona | AsyncDaytona]) -> None:
    client = root(DaytonaConfig(api_key="surface-test")).v2
    lines = snapshot("daytona.api.txt")
    key = key_of(type(client))
    # Instance attributes (the services) included.
    names = sorted(n for n in dir(client) if not n.startswith("_"))
    assert {"create", "get", "list", "find_by_name", "get_limits", "images", "snapshots"} <= set(names)
    missing = [n for n in names if not any(line.startswith(f"{key}.{n}: ") for line in lines)]
    assert missing == []
    assert any(line.startswith(f"{key}.__init__: internal-constructor ") for line in lines)


def test_a_literal_alias_used_without_being_exported_is_expanded() -> None:
    lines = snapshot("daytona.v2.api.txt")
    assert "SignalName" not in __import__("daytona.v2", fromlist=["__all__"]).__all__
    values = ", ".join(repr(v) for v in typing.get_args(v2_types.SignalName))
    assert f"~SignalName: type = Literal[{values}]" in lines
    assert any("signal: SignalName" in line for line in lines if line.startswith("AsyncProcessHandle.kill: "))


@pytest.mark.parametrize(
    "name", ["Resources", "VolumeMount", "TerminalSize", "ComputerUseRegion", "SandboxEventsConfig"]
)
def test_option_dataclasses_users_build_have_public_constructors(name: str) -> None:
    lines = snapshot("daytona.v2.api.txt")
    init = [line for line in lines if line.startswith(f"{name}.__init__: ")]
    assert len(init) == 1
    assert init[0].startswith(f"{name}.__init__: constructor (")


def test_terminal_size_constructor_records_field_order() -> None:
    lines = snapshot("daytona.v2.api.txt")
    assert "TerminalSize.__init__: constructor (cols: int, rows: int) -> None" in lines


def test_handles_users_receive_keep_internal_constructors() -> None:
    lines = snapshot("daytona.v2.api.txt")
    assert any(line.startswith("AsyncSandbox.__init__: internal-constructor ") for line in lines)
    assert any(line.startswith("ImageBuilder.__init__: internal-constructor ") for line in lines)


def test_typeddict_key_required_ness_comes_from_the_annotation() -> None:
    from api_surface import key_required

    assert key_required("str", total=True)
    assert not key_required("str", total=False)
    assert key_required("Required[str]", total=False)
    assert not key_required("NotRequired[str]", total=True)
    assert key_required("ReadOnly[Required[str]]", total=False)


def test_types_callers_pass_in_are_marked() -> None:
    lines = snapshot("daytona.v2.api.txt")
    for name in ("NetworkPolicyInput", "LifecyclePolicyConfig", "TransferCancelSignal"):
        assert f"{name}: passed-in" in lines


def test_a_base_from_the_other_namespace_is_qualified() -> None:
    lines = snapshot("daytona.v2.api.txt")
    assert "DaytonaError: class (daytona.DaytonaError)" in lines


def test_generic_parameters_are_printed() -> None:
    lines = snapshot("daytona.v2.api.txt")
    assert "ListResult: dataclass frozen <T>" in lines


PROBE = """
from __future__ import annotations

from typing import Callable, Protocol, TypedDict

from typing_extensions import NotRequired, Required


class Event(TypedDict):
    name: str


class Filter(TypedDict, total=False):
    name: Required[str]
    label: NotRequired[str]


class Delivered(TypedDict):
    name: str


class Reply(TypedDict):
    ok: bool


class FieldEvent(TypedDict):
    name: str


class Hooks(TypedDict, total=False):
    on_event: Callable[[FieldEvent], Reply]


class Handler(Protocol):
    def __call__(self, ev: Delivered) -> None: ...


class Service:
    def watch(self, on_event: Callable[[Event], None], *, after: Event | None = None) -> None: ...

    def find(self, flt: Filter) -> None: ...

    def subscribe(self, handler: Handler) -> None: ...

    def hook(self, hooks: Hooks, *, after: FieldEvent | None = None) -> None: ...


__all__ = ["Event", "Filter", "Delivered", "Reply", "FieldEvent", "Service"]
"""


def probe_surface(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str = PROBE) -> list[str]:
    import importlib.util
    import sys

    import api_surface
    from api_surface import Surface

    # The generator caches sources and names by module; each test loads a
    # fresh copy of the probe module.
    for cached in (
        api_surface.module_body,
        api_surface.module_imports,
        api_surface.resolve_name,
        api_surface.class_body,
    ):
        cached.cache_clear()

    path = tmp_path / "surface_probe.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("daytona._surface_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "daytona._surface_probe", module)
    spec.loader.exec_module(module)
    return Surface((("daytona._surface_probe", "probe.api.txt", False),)).run()["daytona._surface_probe"]


def test_a_type_also_handed_to_a_callback_is_passed_in_also_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = probe_surface(tmp_path, monkeypatch)
    assert "Event: passed-in also-returned" in lines
    assert "Filter: passed-in" in lines


def test_typeddict_keys_print_without_the_required_wrappers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = probe_surface(tmp_path, monkeypatch)
    assert "Filter.name: key str" in lines
    assert "Filter.label: optional key str" in lines


def test_callback_directions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = probe_surface(tmp_path, monkeypatch)
    # A protocol callback's argument is only handed to the caller.
    assert not any(line.startswith("Delivered: passed-in") for line in lines)
    # A callable field of a passed-in TypedDict: its argument is handed out,
    # its return value is taken in.
    assert "FieldEvent: passed-in also-returned" in lines
    assert "Reply: passed-in" in lines
    assert "~Hooks: passed-in" in lines


def test_alias_values_print_nested_aliases_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = """
from __future__ import annotations

from typing import Callable, Literal, Union

Kind = Literal["a", "b"]
Either = Union[int, str]
OnKind = Callable[[Kind], None]
Pair = tuple[Either, Kind]
Counts = dict[str, int]
_Map = dict[str, int]


def use(m: _Map) -> None: ...


__all__ = ["Kind", "OnKind", "Pair", "Counts", "use"]
"""
    lines = probe_surface(tmp_path, monkeypatch, source)
    assert "OnKind: type = Callable[[Kind], None]" in lines
    # A parameterised builtin is an alias, not a class (Python 3.10 says
    # `isinstance(dict[str, int], type)`).
    assert "Pair: type = tuple[Either, Kind]" in lines
    assert "Counts: type = dict[str, int]" in lines
    assert "~_Map: type = dict[str, int]" in lines
    # A nested alias that is not exported is listed on its own.
    assert "~Either: type = int | str" in lines
