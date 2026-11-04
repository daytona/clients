# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Project constructors cannot inherit SDK config trust by name alone."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_url_write_transactions import migrate

from daytona_upgrade import cli
from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize("client", ["Daytona", "AsyncDaytona"])
@pytest.mark.parametrize("intermediate", [False, True])
@pytest.mark.parametrize("drop", [False, True])
def test_config_escapes_when_subclass_constructor_mutates_it(
    client: str, intermediate: bool, drop: bool
) -> None:
    # Given a config whose URL changes only inside a project constructor.
    source = (
        f"import os\nfrom daytona import {client}, DaytonaConfig\n"
        f"class Base({client}):\n"
        "    def __init__(self, config):\n"
        "        config.api_url = os.environ['X']\n"
        "        super().__init__(config)\n"
        + ("class Custom(Base): pass\n" if intermediate else "Custom = Base\n")
        + "cfg = DaytonaConfig()\nCustom(cfg).create()\n"
    )
    # When checking config provenance with or without the explicit Cloud assertion.
    result = migrate(source, drop=drop)
    # Then the existing refusal/manual-config path handles the escape.
    assert [item["kind"] for item in result.items] == [
        "config-field" if drop else "custom-api-url"
    ]


@pytest.mark.parametrize(
    "body",
    [
        "pass",
        "def __init__(self, config):\n        super().__init__(config)",
        "def __init__(self, config):\n        super().__init__(config=config)",
        "def __init__(self, *args, **kwargs):\n        super().__init__(*args, **kwargs)",
    ],
)
def test_config_resolves_when_entire_subclass_chain_is_transparent(body: str) -> None:
    # Given plain or strictly forwarding constructors across two project bases.
    source = (
        "from daytona import Daytona, DaytonaConfig\n"
        f"class Base(Daytona):\n    {body}\n"
        "class Custom(Base): pass\ncfg = DaytonaConfig()\nCustom(cfg).create()\n"
    )
    # When migrating the inherited call.
    result = migrate(source)
    # Then ordinary subclass configs keep their existing automatic resolution.
    assert result.items == ()
    assert result.source == source.replace(
        "Custom(cfg).create", "Custom(cfg).v0.create"
    )


@pytest.mark.parametrize(
    "body",
    [
        "def __new__(cls, config): return super().__new__(cls)",
        "def __init_subclass__(cls): pass",
        "def __init__(self, config):\n        super().__init__(other)",
        "def __init__(self, config):\n        super().__init__(config.copy())",
        "def __init__(self, config):\n        super().__init__()",
        "def __init__(self, config):\n        super().__init__(config)\n    __init__ = customize",
        "def __init__(self, config):\n        super().__init__(config)\n    def __init__(self, config):\n        config.api_url = url",
    ],
)
def test_config_refuses_when_construction_is_not_proven_transparent(body: str) -> None:
    # Given a project construction hook or a changed forwarding argument.
    source = (
        "from daytona import Daytona, DaytonaConfig\n"
        f"class Custom(Daytona):\n    {body}\n"
        "cfg = DaytonaConfig()\nCustom(cfg)\n"
    )
    # When checking whether the config can escape.
    result = migrate(source)
    # Then inheritance alone cannot certify its API URL.
    assert [item["kind"] for item in result.items] == ["custom-api-url"]


@pytest.mark.parametrize(
    "argument", ["DaytonaConfig()", "config=DaytonaConfig()", "config=cfg"]
)
def test_config_refuses_when_unsafe_constructor_receives_inline_or_keyword_config(
    argument: str,
) -> None:
    # Given a mutating subclass called without a positional config name.
    source = (
        "from daytona import Daytona, DaytonaConfig\nclass Custom(Daytona):\n"
        "    def __init__(self, config):\n        config.api_url = url\n        super().__init__(config)\n"
        f"cfg = DaytonaConfig()\nCustom({argument})\n"
    )
    # When checking the actual constructor boundary.
    result = migrate(source)
    # Then even an immediately constructed config cannot inherit SDK trust.
    assert [item["kind"] for item in result.items] == ["custom-api-url"]


def test_config_refuses_when_mutating_constructor_is_imported() -> None:
    # Given an inherited mutation crossing two project module boundaries.
    sources = {
        "app": "from daytona import DaytonaConfig\nfrom bridge import Custom\ncfg = DaytonaConfig()\nCustom(cfg)\n",
        "bridge": "from base import Base\nclass Custom(Base): pass\n",
        "base": "from daytona import Daytona\nclass Base(Daytona):\n    def __init__(self, config):\n        config.api_url = url\n        super().__init__(config)\n",
    }
    mapping = load_mapping()
    # When the consumer receives constructor trust through the project index.
    result = transform_source(
        sources["app"],
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index(sources, mapping)),
    )
    # Then import/export inference preserves the refusal.
    assert [item["kind"] for item in result.items] == ["custom-api-url"]


def test_cli_refuses_atomically_when_subclass_mutates_config(tmp_path: Path) -> None:
    # Given the reported mutation and an upgradeable dependency.
    source = (
        "import os\nfrom daytona import Daytona, DaytonaConfig\n"
        "class Custom(Daytona):\n    def __init__(self, config):\n"
        "        config.api_url = os.environ['X']\n        super().__init__(config)\n"
        "cfg = DaytonaConfig()\nCustom(cfg).create()\n"
    )
    app = tmp_path / "app.py"
    app.write_text(source)
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("daytona==0.224.0\n")
    # When running the real CLI without a Cloud assertion.
    status = cli.main([str(tmp_path), "--force", "--yes"])
    # Then neither source nor dependencies are partially upgraded.
    assert status == 2
    assert app.read_text() == source
    assert requirements.read_text() == "daytona==0.224.0\n"
