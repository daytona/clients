# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Member replacements survive class summaries, helper calls and loop edges."""

from __future__ import annotations

import ast
import subprocess
import sys
from textwrap import dedent

import pytest
from test_flow import migrate

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize("method", ["__init__", "install", "async def install"])
@pytest.mark.parametrize(
    "write",
    [
        "self.{member} = lambda: 'stub'",
        "setattr(self, '{member}', lambda: 'stub')",
        "object.__setattr__(self, '{member}', lambda: 'stub')",
    ],
)
@pytest.mark.parametrize("member", ["create", "extra"])
def test_subclass_dispatch_when_instance_member_is_written(
    method: str, write: str, member: str
) -> None:
    # Given an instance write in a constructor or another class method.
    declaration = method if method.startswith("async") else "def " + method
    source = (
        "from daytona import Daytona\nclass Custom(Daytona):\n"
        f"    {declaration}(self):\n        {write.format(member=member)}\n"
        "Custom().create()\n"
    )
    # When migrating the downstream constructor result.
    result = migrate(source)
    # Then moved replacements require review; unrelated stores keep SDK dispatch.
    if member == "create":
        assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
        assert any(item["kind"] == "fixme" for item in result.items)
    else:
        assert "Custom().v0.create()" in result.source
        assert result.items == ()


@pytest.mark.parametrize("member", ["create", "extra"])
@pytest.mark.parametrize("callee", ["install", "alias"])
@pytest.mark.parametrize(
    "write", ["client.{member} = stub", "setattr(client, '{member}', stub)"]
)
def test_dispatch_when_parameter_mutating_helper_is_called(
    member: str, callee: str, write: str
) -> None:
    # Given a helper that mutates its annotated client parameter.
    source = (
        "from daytona import Daytona\ndef install(client: Daytona):\n"
        f"    {write.format(member=member)}\n"
        f"alias = install\nclient = Daytona()\n{callee}(client)\nclient.create()\n"
    )
    # When migrating the call after that helper has run.
    result = migrate(source)
    # Then the helper's member-specific effect cannot vanish with its locals.
    if member == "create":
        assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
        assert any(item["kind"] == "fixme" for item in result.items)
    else:
        assert "client.v0.create()" in result.source
        assert result.items == ()


@pytest.mark.parametrize("member", ["create", "extra"])
@pytest.mark.parametrize("loop", ["for _ in range(2):", "while active:"])
@pytest.mark.parametrize("edge", ["", "    continue\n"])
def test_dispatch_when_loop_backedge_carries_member_replacement(
    member: str, loop: str, edge: str
) -> None:
    # Given a call preceding a replacement that a later iteration can observe.
    source = (
        "from daytona import Daytona\nclient = Daytona()\n"
        f"{loop}\n    client.create()\n    client.{member} = stub\n{edge}"
    )
    # When joining loop backedges before transforming its call.
    result = migrate(source)
    # Then only replacement of the called moved member blocks automatic routing.
    if member == "create":
        assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
        assert any(item["kind"] == "fixme" for item in result.items)
    else:
        assert "client.v0.create()" in result.source
        assert result.items == ()


@pytest.mark.parametrize(
    "body",
    [
        """\
class Custom(Daytona):
    def __init__(self):
        self.create = lambda: 'stub'
assert Custom().create() == 'stub'
""",
        """\
def install(client: Daytona):
    client.create = lambda: 'stub'
client = Daytona()
install(client)
assert client.create() == 'stub'
""",
        """\
client = Daytona()
values = []
for _ in range(2):
    values.append(client.create())
    client.create = lambda: 'stub'
assert values == ['sdk', 'stub']
""",
    ],
)
def test_migrated_program_preserves_replacement_dispatch_at_runtime(body: str) -> None:
    # Given an SDK double whose root and v0 deliberately have separate dispatch.
    source = "from daytona import Daytona\n" + body
    runtime = dedent("""\
        import sys
        import types
        class Compatibility:
            def create(self): return 'sdk'
        class Daytona:
            def create(self): return 'sdk'
            @property
            def v0(self): return Compatibility()
        sdk = types.ModuleType('daytona')
        sdk.Daytona = Daytona
        sys.modules['daytona'] = sdk
        """)
    # When running the migrated artifact through the real Python interpreter.
    completed = subprocess.run(
        [sys.executable, "-c", runtime + migrate(source).source],
        capture_output=True,
        text=True,
        check=False,
    )
    # Then routing never bypasses the program's replacement callable.
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("called", [False, True])
def test_helper_effect_when_call_is_present_or_absent(called: bool) -> None:
    # Given a replacement helper whose mere definition does not execute its body.
    source = (
        "from daytona import Daytona\ndef install(client: Daytona):\n"
        "    client.create = stub\nclient = Daytona()\n"
        + ("install(client)\n" if called else "")
        + "client.create()\n"
    )
    # When migrating the client consumer.
    result = migrate(source)
    # Then the effect is applied at the helper call, not at its definition.
    assert ("client.v0.create()" in result.source) is not called


@pytest.mark.parametrize("early_return", [False, True])
def test_dispatch_when_helper_replacement_is_on_an_exiting_path(
    early_return: bool,
) -> None:
    # Given a wrapper around a helper with a conditional or unconditional write.
    write = (
        "    if active:\n        client.create = stub\n        return\n"
        if early_return
        else "    client.create = stub\n"
    )
    source = (
        "from daytona import Daytona\ndef install(client: Daytona):\n"
        + write
        + "def wrapper(client: Daytona):\n    install(client)\n"
        "client = Daytona()\nwrapper(client)\nclient.create()\n"
    )
    # When composing the helper effects at the wrapper call.
    result = migrate(source)
    # Then even a normally returning branch retains the possible replacement.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert any(item["kind"] == "fixme" for item in result.items)


@pytest.mark.parametrize("member", ["create", "extra"])
def test_dispatch_when_loop_backedge_calls_replacement_helper(member: str) -> None:
    # Given a helper effect rather than a syntactic member store in the loop.
    source = (
        "from daytona import Daytona\ndef install(client: Daytona):\n"
        f"    client.{member} = stub\nclient = Daytona()\n"
        "for _ in range(2):\n    client.create()\n    install(client)\n"
    )
    # When replaying the loop's helper calls to its entry.
    result = migrate(source)
    # Then synthetic effects participate even without an assigned loop name.
    assert ("client.v0.create()" in result.source) is (member == "extra")


@pytest.mark.parametrize("member", ["create", "extra"])
def test_dispatch_when_imported_parent_records_instance_write(member: str) -> None:
    # Given an instance-member effect inherited through an imported project base.
    sources = {
        "base": "from daytona import Daytona\nclass Parent(Daytona):\n"
        f"    def install(receiver):\n        receiver.{member} = stub\n",
        "app": "from base import Parent\nclass Custom(Parent): pass\nCustom().create()\n",
    }
    mapping = load_mapping()
    # When migrating a separately indexed subclass consumer.
    result = transform_source(
        sources["app"],
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then class serialization preserves the specific replaced member.
    assert ("Custom().v0.create()" in result.source) is (member == "extra")


def test_dispatch_when_instance_member_has_only_a_local_annotation() -> None:
    # Given a local attribute annotation that performs no runtime store.
    source = (
        "from daytona import Daytona\nclass Custom(Daytona):\n"
        "    def declare(self):\n        self.create: Callable\nCustom().create()\n"
    )
    # When migrating the unchanged inherited SDK method.
    result = migrate(source)
    # Then declarations do not invent replacement effects.
    assert result.source == source.replace("Custom().create()", "Custom().v0.create()")
    assert result.items == ()
