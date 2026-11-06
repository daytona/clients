# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Lazy SDK module factories share ordinary function and project summaries."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


def test_rewrites_error_checks_when_harbor_lazily_imports_daytona() -> None:
    # Given Harbor's real helper and exception-swallowing error checks, verbatim.
    source = (Path(__file__).parent / "fixtures/harbor_utils.py").read_text()
    # When upgrading with the normal project summaries.
    result = migrate(source)
    # Then every check still reaches its error type after the namespace move.
    assert result.source == source.replace("daytona.common", "daytona.v0.common")
    assert result.items == ()


@pytest.mark.parametrize(
    "provider",
    [
        "def load() -> Any:\n    import daytona\n    return daytona\n",
        "import daytona as sdk\ndef load():\n    return sdk\n",
        "def load():\n    import daytona as sdk\n    alias = sdk\n    return alias\n",
        "import importlib\ndef load():\n    return importlib.import_module('daytona')\n",
        "from importlib import import_module as im\ndef load():\n    return im('daytona')\n",
        "class Provider:\n    @staticmethod\n    def load() -> Any:\n        import daytona\n        return daytona\nload = Provider.load\n",
        "class Provider:\n    @classmethod\n    def load(cls):\n        import daytona\n        return daytona\nload = Provider.load\n",
    ],
)
@pytest.mark.parametrize("receiver", ["load()", "module", "alias"])
def test_rewrites_private_access_when_factory_returns_only_module(
    provider: str, receiver: str
) -> None:
    # Given local, imported, aliased and class-owned module return paths.
    source = (
        "from typing import Any\n"
        + provider
        + "module = load()\nalias = module\n"
        + f"error = {receiver}.common.errors.DaytonaError\n"
        + f"client = {receiver}.AsyncDaytona\n"
    )
    # When following the return through a call or its assignment aliases.
    result = migrate(source)
    # Then private paths move once, while client constructors stay at the root.
    assert ast.dump(ast.parse(result.source)) == ast.dump(
        ast.parse(source.replace(f"{receiver}.common", f"{receiver}.v0.common"))
    )
    assert not any(item["kind"] in {"fixme", "private-import"} for item in result.items)


@pytest.mark.parametrize(
    "body",
    [
        "if flag:\n        return daytona\n    return None",
        "if flag:\n        return daytona",
        "module = daytona if flag else None\n    return module",
        "return opaque(daytona)",
    ],
)
def test_reviews_private_access_when_module_return_is_uncertain(body: str) -> None:
    # Given linked module identity without a proof for every returned value.
    source = (
        "import daytona\ndef load(flag):\n    " + body + "\n"
        "module = load(True)\nerror = module.common.errors.DaytonaError\n"
    )
    # When upgrading an unprovable private-path receiver.
    result = migrate(source)
    # Then executable code stays intact, but the private access cannot be missed.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert result.items[0]["line"] == len(source.splitlines())
    assert "# FIXME(daytona-upgrade):" in result.source


@pytest.mark.parametrize("imported", ["from provider import load", "import provider"])
def test_rewrites_private_access_when_factory_is_imported(imported: str) -> None:
    # Given an indexed provider and a consumer without its own SDK import.
    call = "provider.load" if imported == "import provider" else "load"
    source = imported + f"\nmodule = {call}()\nmodule._async.sandbox.AsyncSandbox\n"
    mapping = load_mapping()
    sources = {
        "app": source,
        "provider": "def load() -> Any:\n    import daytona as sdk\n    return sdk\n",
    }
    # When resolving through the existing project factory summaries.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then cross-module module identity has the same rewrite as a direct import.
    assert result.source == source.replace("module._async", "module.v0._async")
    assert result.items == ()


@pytest.mark.parametrize(
    "member", ["common", "_async", "_sync", "_utils", "handle", "internal"]
)
def test_avoids_missing_export_when_root_member_is_bundled_module(member: str) -> None:
    # Given a v0 package path, not a missing public root export.
    source = f"import daytona\nvalue = daytona.{member}.Something\n"
    # When moving the private path into v0.
    result = migrate(source)
    # Then there is no misleading recommendation to upgrade 0.x.
    assert result.source == source.replace(f"daytona.{member}", f"daytona.v0.{member}")
    assert result.items == ()


def test_reports_missing_export_when_name_is_not_a_bundled_module() -> None:
    # Given a genuine unavailable name, rather than a package directory.
    source = "import daytona\nvalue = daytona.MissingExport\n"
    # When migrating the root reference.
    result = migrate(source)
    # Then the report retains the actionable unavailable-export diagnostic.
    assert [(item["kind"], item["message"]) for item in result.items] == [
        (
            "private-import",
            f"MissingExport is not exported by bundled v{load_mapping().v0_version}; use the v2 API or remove it. It is unavailable from daytona.v0.",
        )
    ]
