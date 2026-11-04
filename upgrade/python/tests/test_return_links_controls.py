# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Return evidence stays scoped to its actual consumer and current binding."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "def get_client():\n    return unknown()\nclient = get_client()\nclient.create()\n",
            id="unknown-factory",
        ),
        pytest.param(
            "async def get_client():\n    return unknown()\nasync def run():\n    client = await get_client()\n    await client.create()\n",
            id="unknown-async-factory",
        ),
        pytest.param(
            "import httpx\ndef get_client():\n    return httpx.Client()\nclient = get_client()\nclient.get('url')\n",
            id="http-factory",
        ),
        pytest.param(
            "class Service:\n    def __init__(self):\n        self.http_client = unknown()\n    def run(self):\n        return self.http_client.get('url')\n",
            id="unknown-http-client-field",
        ),
        pytest.param(
            "import httpx\nclass Service:\n    def __init__(self):\n        self.http_client = httpx.Client()\n    def run(self):\n        return self.http_client.get('url')\n",
            id="known-http-client-field",
        ),
        pytest.param(
            "def get_client():\n    return Daytona()\nget_client = unknown\nclient = get_client()\nclient.create()\n",
            id="overridden-factory",
        ),
        pytest.param(
            "def get_client():\n    return Daytona()\nclient = get_client()\nclient = unknown()\nclient.create()\n",
            id="overridden-result",
        ),
        pytest.param(
            "def get_client():\n    return Daytona()\ndef run(get_client):\n    client = get_client()\n    return client.create()\n",
            id="shadowed-factory-parameter",
        ),
        pytest.param(
            "def linked():\n    client = Daytona()\n    return client\ndef unrelated():\n    client = unknown()\n    return client.create()\n",
            id="sibling-local-isolation",
        ),
        pytest.param(
            "def get_client():\n    def nested():\n        return Daytona()\n    return unknown()\nclient = get_client()\nclient.create()\n",
            id="nested-return-isolation",
        ),
        pytest.param(
            "def run(client=Daytona()):\n    client = unknown()\n    return client.create()\n",
            id="overridden-default",
        ),
        pytest.param(
            "def client():\n    return Daytona()\ndef test_x(client):\n    return client.create()\n",
            id="ordinary-function-is-not-fixture",
        ),
        pytest.param(
            "import pytest\n@pytest.fixture\ndef client():\n    return unknown()\ndef test_x(client):\n    return client.create()\n",
            id="unknown-fixture-return",
        ),
        pytest.param(
            "from fastapi import Depends\ndef get_client():\n    return unknown()\ndef run(client=Depends(get_client)):\n    return client.create()\n",
            id="unknown-dependency-return",
        ),
    ],
)
def test_stays_clean_when_current_receiver_has_no_client_link(body: str) -> None:
    # Given an unrelated SDK import, not evidence about these current receivers.
    source = "from daytona import Daytona\n" + body
    # When following factory and injection relationships.
    result = migrate(source)
    # Then neither comments nor executable code are changed by generic names.
    assert result.source == source
    assert result.items == ()


@pytest.mark.parametrize(
    "value,access",
    [
        ("[Daytona()]", "self.pool[0].create()"),
        ('{"eu": Daytona()}', 'self.pool["eu"].create()'),
        ("[AsyncDaytona()]", "self.pool[0].create()"),
    ],
)
@pytest.mark.parametrize("class_scope", [False, True])
def test_requires_review_when_computed_store_carries_client_collection(
    value: str, access: str, class_scope: bool
) -> None:
    # Given a computed field name storing actual client-bearing collection data.
    body = f"def run(self, key):\n    setattr(self, key, {value})\n    {access}\n"
    if class_scope:
        body = (
            "class Service:\n"
            + "\n".join("    " + line for line in body.splitlines())
            + "\n"
        )
    source = "from daytona import Daytona, AsyncDaytona\n" + body
    # When inspecting the element reached through an uncertain attribute.
    result = migrate(source)
    # Then the linked element is reviewable without asserting the selected field.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert "# FIXME(daytona-upgrade):" in result.source
