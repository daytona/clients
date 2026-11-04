# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Client-returning methods keep their proof through annotated singleton factories."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade.cli import main


@pytest.mark.parametrize("receiver", ["manager", "env._client_manager"])
@pytest.mark.parametrize("returned", ["self._client", "unknown()"])
@pytest.mark.parametrize("annotation", ["SDK", "'SDK'"])
def test_routes_when_imported_singleton_method_declares_client(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    receiver: str,
    returned: str,
    annotation: str,
) -> None:
    # Given a cross-module singleton and either a local or holder-field caller.
    (tmp_path / "service.py").write_text(
        "try:\n    from daytona import AsyncDaytona as SDK, DaytonaConfig\n"
        "except ImportError:\n    pass\n"
        "class DaytonaClientManager:\n"
        "    _instance = None\n"
        "    @classmethod\n"
        "    async def get_instance(cls) -> 'DaytonaClientManager':\n"
        "        if cls._instance is None: cls._instance = cls()\n"
        "        return cls._instance\n"
        f"    async def get_client(self) -> {annotation}:\n"
        "        async with self._client_lock:\n"
        "            if self._client is None:\n"
        "                if configured: self._client = SDK(DaytonaConfig(connection_pool_maxsize=10))\n"
        "                else: self._client = SDK()\n"
        f"            return {returned}\n"
        "async def command(sandbox): await sandbox.process.exec('true')\n"
    )
    app = tmp_path / "app.py"
    source = (
        "import asyncio\nfrom service import DaytonaClientManager as Manager\n"
        "async def run(env, reference, params):\n"
        f"    {receiver} = await Manager.get_instance()\n"
        f"    daytona = await {receiver}.get_client()\n"
        "    sandbox = await daytona.get(reference.sandbox_id)\n"
        "    create_task = asyncio.ensure_future(daytona.create(params=params, timeout=60))\n"
    )
    app.write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")

    # When migrating through the real CLI and shared project summaries.
    status = main([str(tmp_path), "--json", "--strict", "--force", "--yes"])

    # Then both Harbor call shapes route through v0 without a manual item.
    report = json.loads(capsys.readouterr().out)
    assert status == 0
    assert report["items"] == []
    assert app.read_text() == source.replace("daytona.get(", "daytona.v0.get(").replace(
        "daytona.create(", "daytona.v0.create("
    )


@pytest.mark.parametrize(
    "change",
    [
        "manager = unknown()",
        "manager = Other()",
        "Manager.get_client = unknown",
    ],
)
def test_preserves_call_when_singleton_proof_is_replaced(change: str) -> None:
    # Given a declared client-returning method whose receiver or method is replaced.
    source = (
        "from daytona import AsyncDaytona\nclass Other:\n"
        "    async def get_client(self): return unknown()\n"
        "class Manager:\n    @classmethod\n"
        "    async def get_instance(cls) -> 'Manager': return unknown()\n"
        "    async def get_client(self) -> AsyncDaytona: return unknown()\n"
        "async def run():\n    manager = await Manager.get_instance()\n"
        f"    {change}\n    daytona = await manager.get_client()\n"
        "    await daytona.get('id')\n"
    )
    # When the shared resolver sees the conflicting value or class mutation.
    result = migrate(source)
    # Then a matching method name cannot revive the original client proof.
    assert "await daytona.get('id')" in result.source
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize(
    "fallback", ["SDK = foreign", "from foreign import AsyncDaytona as SDK"]
)
def test_preserves_call_when_optional_annotation_has_foreign_writer(
    fallback: str,
) -> None:
    # Given a same-spelled client annotation that may resolve to a foreign type.
    source = (
        "try:\n    from daytona import AsyncDaytona as SDK\n"
        f"except ImportError:\n    {fallback}\n"
        "class Manager:\n    @classmethod\n"
        "    async def get_instance(cls) -> 'Manager': return unknown()\n"
        "    async def get_client(self) -> SDK: return unknown()\n"
        "async def run():\n    manager = await Manager.get_instance()\n"
        "    daytona = await manager.get_client()\n    await daytona.get('id')\n"
    )
    # When resolving the annotation through the existing import-only proof.
    result = migrate(source)
    # Then an alternative writer leaves the receiver manual, never rewritten.
    assert "await daytona.get('id')" in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


def test_preserves_call_when_optional_annotation_is_locally_shadowed() -> None:
    # Given a sole-writer module import hidden by a function-local binding.
    source = (
        "try:\n    from daytona import AsyncDaytona as SDK\n"
        "except ImportError:\n    pass\n"
        "def outer():\n    if flag:\n        from foreign import AsyncDaytona as SDK\n"
        "    else:\n        from daytona import AsyncDaytona as SDK\n"
        "    def make() -> SDK: return unknown()\n"
        "    daytona = make()\n    daytona.get('id')\n"
    )
    # When the inner annotation resolves against local, not module bindings.
    result = migrate(source)
    # Then module import evidence cannot certify the shadowing local type.
    assert "daytona.get('id')" in result.source
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize("builtin", ["builtins", "bi", "alias"])
def test_reviews_optional_annotation_when_builtin_exec_can_replace_import(
    builtin: str,
) -> None:
    # Given namespace execution, including imported and assigned builtins aliases.
    source = (
        "import builtins\nimport builtins as bi\nalias = bi\n"
        "try:\n    from daytona import AsyncDaytona as SDK\n"
        "except ImportError:\n    pass\n"
        f"{builtin}.exec(code)\n"
        "def make() -> SDK: return unknown()\n"
        "daytona = make()\ndaytona.get('id')\n"
    )
    # When the import-only scanner distinguishes a real builtin from process.exec.
    result = migrate(source)
    # Then opaque namespace mutation cannot supply a client annotation proof.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "replacement", ["self._client_manager.get_client()", "foreign.Client()"]
)
def test_routes_parameter_fallback_only_when_value_does_not_contradict_contract(
    replacement: str,
) -> None:
    # Given Harbor's nullable instance field and optional client-parameter fallback.
    source = (
        "try:\n    from daytona import AsyncDaytona\n"
        "except ImportError:\n    pass\n"
        "from tenacity import retry\nimport foreign\nclass Manager:\n"
        "    async def get_client(self) -> AsyncDaytona: return unknown()\n"
        "class Environment:\n    def __init__(self):\n"
        "        self._client_manager: Manager | None = None\n"
        "    @retry(reraise=True)\n"
        "    async def create(self, daytona: 'AsyncDaytona | None' = None):\n"
        "        if daytona is None:\n"
        "            if not self._client_manager: raise RuntimeError()\n"
        f"            daytona = await {replacement}\n"
        "        self.labels()\n        await daytona.create(params=params, timeout=60)\n"
    )
    # When an opaque fallback is assigned to the explicitly annotated parameter.
    result = migrate(source)
    # Then the declared contract survives opacity, but never a concrete foreign client.
    expected = (
        source.replace("daytona.create(", "daytona.v0.create(")
        if replacement == "self._client_manager.get_client()"
        else source
    )
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(expected))
    assert [item["kind"] for item in result.items] == (
        [] if replacement == "self._client_manager.get_client()" else ["fixme"]
    )
