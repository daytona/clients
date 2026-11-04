# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Unannotated client return paths supply review evidence, never rewrite proof."""

from __future__ import annotations

import ast
from typing import Final

import pytest
from test_flow import migrate as migrate_app
from upgrade_helpers import migrate_test_module

HEADER: Final = "from daytona import Daytona, AsyncDaytona, DaytonaConfig\n"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "def get_client():\n    return Daytona()\nclient = get_client()\ndef run():\n    return client.create()\n",
            id="factory-same-module",
        ),
        pytest.param(
            "import functools\n@functools.lru_cache\ndef get_client():\n    return Daytona()\nclient = get_client()\ndef run():\n    return client.create()\n",
            id="factory-lru-cache",
        ),
        pytest.param(
            "from functools import cache\n@cache\ndef get_client():\n    return Daytona()\nclient = get_client()\ndef run():\n    return client.create()\n",
            id="factory-cache",
        ),
        pytest.param(
            "def get_client():\n    return AsyncDaytona()\nclient = get_client()\nasync def run():\n    return await client.create()\n",
            id="async-client-sync-factory",
        ),
        pytest.param(
            "async def get_client():\n    return AsyncDaytona()\nasync def run():\n    client = await get_client()\n    return await client.create()\n",
            id="async-client-async-factory",
        ),
        pytest.param(
            'def get_client(flag):\n    config = DaytonaConfig(api_key="review-offline")\n    if flag:\n        return Daytona(config)\n    else:\n        return Daytona(config)\nclient = get_client(True)\ndef run():\n    return client.create()\n',
            id="factory-if-else",
        ),
        pytest.param(
            "class Provider:\n    @classmethod\n    def get_client(cls):\n        return Daytona()\nclient = Provider.get_client()\ndef run():\n    return client.create()\n",
            id="factory-classmethod",
        ),
        pytest.param(
            "class Provider:\n    @staticmethod\n    def get_client():\n        return Daytona()\nclient = Provider.get_client()\ndef run():\n    return client.create()\n",
            id="factory-staticmethod",
        ),
        pytest.param(
            "def run(x=True):\n    client = Daytona() if x else None\n    return client.create()\n",
            id="conditional-client",
        ),
        pytest.param(
            "def run(client=Daytona()):\n    return client.create()\n",
            id="default-client",
        ),
        pytest.param(
            "client = None\ndef initialize():\n    global client\n    client = Daytona()\ndef run():\n    initialize()\n    return client.create()\n",
            id="global-client",
        ),
        pytest.param(
            "import pytest\n@pytest.fixture\ndef client():\n    return Daytona()\ndef test_x(client):\n    return client.create()\n",
            id="pytest-return-fixture",
        ),
        pytest.param(
            "import pytest\n@pytest.fixture\ndef client():\n    yield Daytona()\ndef test_x(client):\n    return client.create()\n",
            id="pytest-yield-fixture",
        ),
        pytest.param(
            'from fastapi import Depends, FastAPI\nweb = FastAPI()\ndef get_client():\n    return Daytona()\n@web.get("/job")\ndef job(client=Depends(get_client)):\n    return client.create()\n',
            id="fastapi-depends",
        ),
        pytest.param(
            "def make_client():\n    return Daytona()\nclass Service:\n    def __init__(self):\n        self.client = make_client()\n    def run(self):\n        return self.client.create()\n",
            id="self-factory-client",
        ),
    ],
)
def test_requires_review_when_unannotated_value_flow_links_client(body: str) -> None:
    # Given an actual Daytona construction linked to an unproven receiver.
    source = HEADER + body
    # When migrating through the normal source/project-index entry point.
    # pytest injects fixtures only into collected modules (test_*.py).
    migrate = migrate_test_module if "@pytest.fixture" in body else migrate_app
    result = migrate(source)
    # Then comments report manual work without changing executable semantics.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert "# FIXME(daytona-upgrade):" in result.source
    assert migrate(result.source).source == result.source
