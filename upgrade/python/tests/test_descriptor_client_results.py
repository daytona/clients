# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Descriptor reads must prove a client or use the normal manual-review rule."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "descriptor",
    [
        ("", "property"),
        ("from builtins import property as prop\n", "prop"),
        ("from functools import cached_property\n", "cached_property"),
        ("import functools as ft\n", "ft.cached_property"),
    ],
)
@pytest.mark.parametrize("annotation", ["", " -> Daytona", " -> 'Daytona'"])
@pytest.mark.parametrize("receiver", ["self.client", "app.client", "App().client"])
def test_routes_when_property_returns_proven_client(
    descriptor: tuple[str, str], annotation: str, receiver: str
) -> None:
    # Given an annotated or directly constructed lazy client.
    imports, decorator = descriptor
    source = (
        "from daytona import Daytona\n" + imports + "class App:\n"
        f"    @{decorator}\n    def client(self){annotation}: return Daytona()\n"
        + (
            "    def run(self): self.client.create()\n"
            if receiver == "self.client"
            else f"app = App()\n{receiver}.create()\n"
        )
    )
    # When migrating descriptor access, not a method call.
    result = migrate(source)
    # Then executable calls route through v0 without manual review.
    assert result.source == source.replace(
        f"{receiver}.create()", f"{receiver}.v0.create()"
    )
    assert not result.items


@pytest.mark.parametrize("decorator", ["property", "cached_property"])
@pytest.mark.parametrize("receiver", ["self.client", "app.client"])
@pytest.mark.parametrize(
    "body",
    ["return unknown()", "if condition: return Daytona()\n        return unknown()"],
)
def test_reports_when_property_client_cannot_be_proven(
    decorator: str, receiver: str, body: str
) -> None:
    # Given a lazy client whose result is not statically known.
    source = (
        "from daytona import Daytona\nfrom functools import cached_property\n"
        f"class App:\n    @{decorator}\n    def client(self):\n        {body}\n"
        + (
            "    def run(self): self.client.create()\n"
            if receiver == "self.client"
            else "app = App()\napp.client.create()\n"
        )
    )
    # When migrating an unproven descriptor receiver.
    result = migrate(source)
    # Then only an actual Daytona return path justifies manual review.
    assert f"{receiver}.v0.create()" not in result.source
    assert [item["kind"] for item in result.items] == (
        [] if body == "return unknown()" else ["fixme"]
    )


@pytest.mark.parametrize(
    "definition,trailer",
    [
        ("async def client(self) -> Daytona: return Daytona()", ""),
        ("def client(self): return Daytona()", "Daytona = Other\n"),
        ("def client(Daytona): return Daytona()", ""),
    ],
)
def test_reports_when_descriptor_result_is_coroutine_or_shadowed(
    definition: str, trailer: str
) -> None:
    # Given a property whose body cannot establish a synchronous client proof.
    source = (
        "from daytona import Daytona\nclass App:\n    @property\n    "
        + definition
        + "\n"
        + trailer
        + "app = App()\napp.client.create()\n"
    )
    # When resolving the descriptor read.
    result = migrate(source)
    # Then review is explicit rather than a false automatic rewrite.
    assert "app.client.v0.create()" not in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "setup,decorators",
    [
        ("def adapt(fn): return lambda: Other()\nproperty = adapt\n", "@property"),
        ("def adapt(fn): return lambda: Other()\n", "@adapt\n    @property"),
    ],
)
def test_keeps_manual_when_property_decorator_is_not_transparent(
    setup: str, decorators: str
) -> None:
    # Given a descriptor or decorator stack that may replace the annotated result.
    source = (
        "from daytona import Daytona\n" + setup + "class App:\n"
        f"    {decorators}\n    def client(self) -> Daytona: return Daytona()\n"
        "app = App()\napp.client.create()\n"
    )
    # When migrating the uncertain descriptor read.
    result = migrate(source)
    # Then no annotation is turned into a false proof and review is explicit.
    assert "app.client.v0.create()" not in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize("returned", ["Daytona()", "unknown()"])
def test_cli_routes_or_reports_when_class_exposes_lazy_client(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], returned: str
) -> None:
    # Given a real project exposing and consuming a cached client.
    source = (
        "from daytona import Daytona\nfrom functools import cached_property\n"
        f"class App:\n    @cached_property\n    def client(self): return {returned}\n"
        "    def run(self): self.client.create()\napp = App()\napp.client.create()\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When the CLI upgrades the project through its public entrypoint.
    code = main([str(tmp_path), "--force", "--yes", "--json"])
    # Then proven reads migrate; unknown-only getters need no invented diagnostics.
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    if returned == "Daytona()":
        assert app.read_text() == source.replace(
            ".client.create()", ".client.v0.create()"
        )
        assert report["items"] == []
    else:
        assert app.read_text() == source
        assert report["items"] == []


def test_reports_when_cached_property_can_be_overwritten() -> None:
    # Given a cached property whose attribute is also assigned an unknown value.
    source = (
        "from daytona import Daytona\nfrom functools import cached_property\n"
        "class App:\n    @cached_property\n"
        "    def client(self) -> Daytona: return Daytona()\n"
        "    def replace(self): self.client = unknown()\n"
        "app = App()\napp.client.create()\n"
    )
    # When migrating a descriptor whose cached instance value is not stable.
    result = migrate(source)
    # Then outside-class access cannot resurrect the getter's obsolete proof.
    assert "app.client.v0.create()" not in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


def test_routes_when_descriptor_name_is_rebound_after_its_definition() -> None:
    # Given an already-created descriptor unaffected by a later decorator-name write.
    source = (
        "from daytona import Daytona\nclass App:\n    @property\n"
        "    def client(self): return Daytona()\n"
        "property = unknown\napp = App()\napp.client.create()\n"
    )
    # When resolving creation-time decorator bindings separately from the getter body.
    result = migrate(source)
    # Then only the return expression needs a deferred runtime proof.
    assert result.source == source.replace(
        "app.client.create()", "app.client.v0.create()"
    )
    assert not result.items


@pytest.mark.parametrize(
    "decorators",
    [
        "@staticmethod\n    @property",
        "@property\n    @classmethod",
        "@property\n    @property",
    ],
)
def test_reports_when_descriptor_stack_does_not_prove_getter_result(
    decorators: str,
) -> None:
    # Given descriptor composition that may expose a descriptor rather than a client.
    source = (
        "from daytona import Daytona\nclass App:\n"
        f"    {decorators}\n    def client(self) -> Daytona: return Daytona()\n"
        "app = App()\napp.client.create()\n"
    )
    # When inspecting the annotated but nontransparent descriptor composition.
    result = migrate(source)
    # Then a function annotation cannot resurrect a rejected property proof.
    assert "app.client.v0.create()" not in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]
