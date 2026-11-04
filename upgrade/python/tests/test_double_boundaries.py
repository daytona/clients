# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Unprovable double layouts remain explicit strict-mode review obligations."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from test_flow import migrate

from daytona_upgrade.cli import main


@pytest.mark.parametrize(
    "factory",
    [
        "SimpleNamespace(create=Mock(), other=42)",
        "SimpleNamespace(**options)",
        "foreign()",
        "Fake()",
    ],
)
@pytest.mark.parametrize(
    "consumer", ["consume(fake)", "patch('app.make', return_value=fake)"]
)
def test_strict_reports_double_when_client_shape_cannot_be_proven(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], factory: str, consumer: str
) -> None:
    # Given a mixed/dynamic/opaque double reaching a proven parameter or patch target.
    source = (
        "from daytona import Daytona\nfrom types import SimpleNamespace\n"
        "from unittest.mock import Mock, patch\n"
        "class Fake(Unknown): pass\n"
        "def consume(client: Daytona): return client.create()\n"
        "def make() -> Daytona: return Daytona()\n"
        f"def test_case():\n    fake = {factory}\n    {consumer}\n"
    )
    (tmp_path / "app.py").write_text(source)
    (tmp_path / "requirements.txt").write_text("daytona==0.224.0\n")
    # When the real CLI applies production rewrites in strict mode.
    status = main([str(tmp_path), "--yes", "--json", "--strict"])
    report = json.loads(capsys.readouterr().out)
    # Then manual adaptation cannot be silently presented as a successful upgrade.
    assert status == 1
    assert any(item["kind"] == "mock-target" for item in report["items"])
    assert f"fake = {factory}" in (tmp_path / "app.py").read_text()


@pytest.mark.parametrize(
    "factory",
    ["Mock()", "MagicMock()", "AsyncMock()", "create_autospec(Daytona, instance=True)"],
)
def test_refuses_collision_when_double_already_configures_v0(factory: str) -> None:
    # Given distinct expectations on root and existing v0 mocks.
    source = (
        "from daytona import Daytona\n"
        "from unittest.mock import Mock, MagicMock, AsyncMock, create_autospec\n"
        "def consume(client: Daytona): return client.create()\n"
        f"def test_case():\n    fake = {factory}\n"
        "    fake.create.return_value = 42\n    fake.v0.create.return_value = 99\n"
        "    assert consume(fake) == 42\n"
    )
    # When moving the root setup would merge two independent mock identities.
    result = migrate(source)
    # Then the producer remains unchanged and requires explicit review.
    assert "fake.create.return_value = 42" in result.source
    assert "fake.v0.create.return_value = 99" in result.source
    assert any(item["kind"] == "mock-target" for item in result.items)


def test_refuses_mock_rewrite_when_parameter_default_overrides_base_contract() -> None:
    # Given a base annotation contradicted by the concrete default's method override.
    source = (
        "from daytona import Daytona\nfrom unittest.mock import MagicMock\n"
        "class Service:\n    def consume(self, client: Daytona): return client.create()\n"
        "class Foreign(Service):\n    def consume(self, client): return client.create()\n"
        "def test_case(service: Service = Foreign()):\n"
        "    daytona = MagicMock()\n    daytona.create.return_value = 42\n"
        "    assert service.consume(daytona) == 42\n"
    )
    # When resolving the dispatched consumer rather than the widened annotation.
    result = migrate(source)
    # Then only the genuine base consumer is rewritten; test setup keeps its FIXME.
    assert "daytona.create.return_value = 42" in result.source
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize(
    "escape",
    ["unknown(fake)", "return fake", "alias = fake", "other = Fake(); unknown(other)"],
)
def test_refuses_class_adaptation_when_any_instance_escapes(escape: str) -> None:
    # Given one proven consumer plus another whole-object flow outside its contract.
    source = (
        "from daytona import Daytona\nclass Fake:\n    def create(self): return 42\n"
        "def consume(client: Daytona): return client.create()\n"
        f"def test_case():\n    fake = Fake()\n    consume(fake)\n    {escape}\n"
    )
    # When deciding whether it is safe to add a class-wide namespace.
    result = migrate(source)
    # Then no property is inserted for only a subset of the class's consumers.
    assert "def v0" not in result.source
    assert any(item["kind"] == "mock-target" for item in result.items)


@pytest.mark.parametrize(
    "source",
    [
        "from types import SimpleNamespace\nfrom unittest.mock import patch\n"
        "def load():\n    import daytona\n    return daytona\n"
        "def test_case():\n    fake = SimpleNamespace()\n    fake.common = object()\n"
        "    patch('app.load', return_value=fake)\n",
        "from unittest.mock import Mock, patch\n"
        "def load():\n    import daytona\n    return daytona\n"
        "def test_case():\n    fake = Mock()\n    fake.common = object()\n"
        "    patch('app.load', return_value=fake)\n",
        "from daytona import Daytona\nfrom types import SimpleNamespace\n"
        "from unittest.mock import Mock\ndef consume(client: Daytona): return client.create()\n"
        "def test_case():\n    fake = SimpleNamespace(create=Mock())\n    consume(fake)\n",
        "from daytona import Daytona\nclass Fake:\n    def create(self): return 42\n"
        "def consume(client: Daytona): return client.create()\n"
        "def test_case():\n    fake = Fake()\n    consume(fake)\n",
    ],
)
def test_double_adaptation_is_idempotent_when_rerun(source: str) -> None:
    # Given code whose local producer and consumer can both be migrated.
    first = migrate(source)
    # When running the migration again on its own output.
    second = migrate(first.source)
    # Then neither new wrappers nor spurious review items appear.
    assert second.source == first.source
    assert not second.items
    assert ast.parse(second.source)


def test_preserves_proof_when_unknown_decorator_shadows_override() -> None:
    # Given a local namesake, not typing's identity decorator.
    source = (
        "from daytona import Daytona\nfrom typing import override\n"
        "override = custom\nclass Service:\n"
        "    def __init__(self): self.client = Daytona()\n"
        "    @override\n    def run(self): return self.client.create()\n"
    )
    # When the decorator's import binding has been replaced.
    result = migrate(source)
    # Then spelling cannot authorize an instance-receiver rewrite.
    assert "self.client.v0" not in result.source


@pytest.mark.parametrize(
    "declaration",
    [
        "def property(value): return value\nclass Fake:\n    def create(self): return 42\n",
        "class Fake:\n    def create(self): return 42\n    def escape(self): return self\n",
        "class Fake:\n    def create(self): return 42\n    def __getattr__(self, name): return 42\n",
    ],
)
def test_reviews_class_when_identity_property_is_not_safe(declaration: str) -> None:
    # Given a shadowed descriptor builtin, self escape, or dynamic namespace hook.
    source = (
        "from daytona import Daytona\n"
        + declaration
        + "def consume(client: Daytona): return client.create()\n"
        "def test_case():\n    fake = Fake()\n    consume(fake)\n"
    )
    # When the class does not meet the self-property proof boundary.
    result = migrate(source)
    # Then inserting a descriptor cannot silently change the fake's behavior.
    assert "def v0" not in result.source
    assert any(item["kind"] == "mock-target" for item in result.items)
