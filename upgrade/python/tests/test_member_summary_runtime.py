# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Run migrated programs against distinct SDK and replacement dispatch paths."""

from __future__ import annotations

import subprocess
import sys
from textwrap import dedent

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "body",
    [
        """\
class Custom(Daytona):
    def __init__(self):
        name = 'create'
        setattr(self, name, lambda: 'stub')
assert Custom().create() == 'stub'
""",
        """\
class Custom(Daytona):
    def __init__(self):
        mapping = {'create': lambda: 'stub'}
        self.__dict__.update(mapping)
assert Custom().create() == 'stub'
""",
        """\
class Custom(Daytona):
    def __init__(self):
        kw = {'create': lambda: 'stub'}
        vars(self).update(**kw)
assert Custom().create() == 'stub'
""",
        """\
class Custom(Daytona):
    @property
    def install(self):
        self.create = lambda: 'stub'
client = Custom()
client.install
assert client.create() == 'stub'
""",
        """\
class Custom(Daytona):
    @classmethod
    def install(cls):
        cls.create = lambda self: 'stub'
Custom.install()
assert Custom().create() == 'stub'
""",
        """\
def install(client: Daytona):
    client.create = lambda: 'stub'
def harmless(client): pass
client = Daytona()
install((install := harmless, client)[1])
assert client.create() == 'stub'
""",
    ],
)
def test_replacement_dispatch_survives_when_migrated_program_runs(body: str) -> None:
    # Given independent root and v0 dispatch, with an observable replacement.
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
    source = "from daytona import Daytona\n" + body
    # When executing the actual transformed source.
    completed = subprocess.run(
        [sys.executable, "-c", runtime + migrate(source).source],
        capture_output=True,
        text=True,
        check=False,
    )
    # Then the original callable still runs rather than the separate v0 method.
    assert completed.returncode == 0, completed.stderr
