# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Inherited SDK members migrate without taking ownership of project overrides."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade import cli
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    "declaration,use",
    [
        ("class MyDaytona(Daytona): pass", "client = MyDaytona()"),
        ("class MyAsync(AsyncDaytona): pass", "client = MyAsync()"),
        (
            "Base = Daytona\nclass Intermediate(Base): pass\nclass MyDaytona(Intermediate): pass",
            "client = MyDaytona()",
        ),
        ("import daytona as sdk\nclass Custom(sdk.Daytona): pass", "client = Custom()"),
        ("class Custom(Daytona): pass\nAlias = Custom", "client = Alias()"),
        ("class Custom(Daytona): pass", "client: Custom"),
    ],
)
def test_inherited_members_migrate_when_constructor_is_project_subclass(
    declaration: str, use: str
) -> None:
    # Given a client subclass proven through the ordinary binding rules.
    source = f"from daytona import Daytona, AsyncDaytona\n{declaration}\n{use}\nclient.create()\nclient.snapshot.list()\n"
    # When migrating inherited methods and services.
    result = migrate(source)
    # Then both route to v0 with no manual work and remain idempotent.
    assert result.source == source.replace("client.create", "client.v0.create").replace(
        "client.snapshot", "client.v0.snapshot"
    )
    assert result.items == ()
    assert migrate(result.source).source == result.source


def test_async_context_routes_when_context_manager_is_subclass() -> None:
    # Given an async subclass used directly as a context manager.
    source = "from daytona import AsyncDaytona as Base\nclass MyAsync(Base): pass\nasync def run():\n    async with MyAsync() as d:\n        await d.create()\n"
    # When migrating the context-bound client.
    result = migrate(source)
    # Then the await still targets the inherited compatibility method.
    assert result.source == source.replace("d.create()", "d.v0.create()")
    assert result.items == ()


@pytest.mark.parametrize("member", ["create()", "snapshot.list()", "warm_pool"])
@pytest.mark.parametrize("base", ["Daytona", "AsyncDaytona"])
def test_self_is_manual_when_member_is_inherited(member: str, base: str) -> None:
    # Given an inherited call inside the subclass, where self.v0 is not a safe fix.
    source = f"from daytona import {base}\nclass Custom({base}):\n    def run(self):\n        return self.{member}\n"
    # When migrating the subclass body.
    result = migrate(source)
    # Then executable code is preserved and strict mode can see the manual item.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert "FIXME(daytona-upgrade)" in result.source
    assert migrate(result.source).source == result.source


@pytest.mark.parametrize(
    "definition,member",
    [
        ("def create(self): return 'custom'", "create()"),
        ("snapshot = custom_service", "snapshot.list()"),
        ("@property\n    def warm_pool(self): return custom_service", "warm_pool"),
    ],
)
@pytest.mark.parametrize("intermediate", [False, True])
def test_override_stays_when_defined_by_project_base(
    definition: str, member: str, intermediate: bool
) -> None:
    # Given a local override, optionally inherited through another project class.
    source = f"from daytona import Daytona\nclass Base(Daytona):\n    {definition}\n    def run(self):\n        return self.{member}\n"
    source += "class Custom(Base): pass\n" if intermediate else "Custom = Base\n"
    source += f"client = Custom()\nclient.{member}\nclient.get('id')\n"
    # When migrating the inherited SDK method alongside the override.
    result = migrate(source)
    # Then only SDK-owned behavior moves, not the project-defined member.
    assert result.source == source.replace("client.get", "client.v0.get")
    assert result.items == ()


def test_cross_module_routes_when_index_knows_aliased_base() -> None:
    # Given cross-module inheritance, re-exported constructors and an override.
    sources = {
        "app": "from bridge import Custom as Client\nd = Client()\nd.create()\nd.snapshot.list()\nd.get('id')\n",
        "bridge": "from base import Intermediate as Parent\nclass Custom(Parent): pass\n",
        "base": "from daytona import Daytona as SDK\nclass Intermediate(SDK):\n    def get(self, name): return name\n",
    }
    mapping = load_mapping()
    # When transforming the consumer with the fixed-point project index.
    result = transform_source(
        sources["app"],
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then inherited SDK members move and the intermediate override stays.
    assert result.source == sources["app"].replace("d.create", "d.v0.create").replace(
        "d.snapshot", "d.v0.snapshot"
    )
    assert result.items == ()


def test_strict_fails_when_subclass_body_needs_manual_migration(tmp_path: Path) -> None:
    # Given a subclass-based CLI project, including its dependency declaration.
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    path = tmp_path / "app.py"
    path.write_text(
        "from daytona import Daytona\nclass Custom(Daytona):\n    def run(self):\n        return self.create()\nclient = Custom()\nclient.create()\n"
    )
    # When upgrading under strict mode.
    status = cli.main([str(tmp_path), "--force", "--yes", "--strict"])
    # Then the external call is migrated but manual class work cannot pass strict.
    assert status == 1
    assert "client.v0.create()" in path.read_text()
    assert "self.create()" in path.read_text()
    assert "FIXME(daytona-upgrade)" in path.read_text()


@pytest.mark.parametrize("builtin", ["getattr", "hasattr"])
def test_lookup_routes_when_subclass_member_is_sdk_owned(builtin: str) -> None:
    # Given a subclass with one project method and one inherited SDK method.
    source = f"from daytona import Daytona\nclass Custom(Daytona):\n    def get(self): pass\nd = Custom()\n{builtin}(d, 'create')\n{builtin}(d, 'get')\n"
    # When migrating the same builtin seam as ordinary SDK clients.
    result = migrate(source)
    # Then only the inherited lookup uses the compatibility namespace.
    assert result.source == source.replace(
        f"{builtin}(d, 'create')", f"{builtin}(d.v0, 'create')"
    )
    assert result.items == ()


def test_mixin_override_stays_when_project_base_precedes_sdk() -> None:
    # Given an intermediate project base which supplies its own create method.
    source = "from daytona import Daytona\nclass Mixin:\n    def create(self): return 'custom'\nclass Custom(Mixin, Daytona): pass\nd = Custom()\nd.create()\nd.get('id')\n"
    # When migrating the multiple-inheritance client.
    result = migrate(source)
    # Then the project override stays while the SDK-owned member moves.
    assert result.source == source.replace("d.get", "d.v0.get")
    assert result.items == ()


def test_custom_config_refuses_when_constructor_is_subclass(tmp_path: Path) -> None:
    # Given a subclass forwarding a config with a known private endpoint.
    source = "from daytona import Daytona, DaytonaConfig\nclass Custom(Daytona): pass\ncfg = DaytonaConfig(api_url='https://private.example/api')\nd = Custom(cfg)\nd.create()\n"
    path = tmp_path / "app.py"
    path.write_text(source)
    # When invoking the actual CLI refusal boundary.
    status = cli.main([str(tmp_path), "--force", "--yes"])
    # Then subclass proof cannot bypass custom endpoint safety or partially write.
    assert status == 2
    assert path.read_text() == source


def test_runtime_succeeds_when_subclass_calls_and_overrides_are_migrated(
    tmp_path: Path,
) -> None:
    # Given an SDK-shaped fake with a root guard and distinct override behavior.
    # It lives outside the project, like an installed SDK.
    sdk = tmp_path / "site" / "daytona"
    sdk.mkdir(parents=True)
    (sdk / "__init__.py").write_text(
        "class V0:\n    def create(self): return 'sdk'\n"
        "class Daytona:\n    v0 = V0()\n"
        "    def __getattr__(self, name): raise AttributeError(name)\n"
    )
    path = tmp_path / "project" / "app.py"
    path.parent.mkdir()
    path.write_text(
        "from daytona import Daytona\nclass Custom(Daytona):\n"
        "    def get(self): return 'override'\n"
        "d = Custom()\nprint(d.create(), d.get())\n"
    )
    assert cli.main([str(path), "--force", "--yes"]) == 0
    # When running the migrated program through its real Python entrypoint.
    run = subprocess.run(
        [sys.executable, str(path)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(sdk.parent)},
    )
    # Then the inherited call reaches v0 and the project override stays on root.
    assert run.returncode == 0, run.stderr
    assert run.stdout == "sdk override\n"
