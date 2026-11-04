# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Client fallback expressions retain proof without guessing mixed value flows."""

from __future__ import annotations

import os
import subprocess

import pytest
from test_dataclass_instances import migrate


@pytest.mark.parametrize(
    "value",
    [
        "client or Daytona()",
        "client if client is not None else Daytona()",
        "Daytona() if client is None else client",
    ],
)
@pytest.mark.parametrize("annotation", ["", ": Daytona"])
def test_routes_field_when_optional_client_has_constructor_fallback(
    value: str, annotation: str
) -> None:
    # Given an injected optional client and the actual bundled 2.x root.
    interpreter = os.environ.get("DAYTONA_UPGRADE_TEST_SDK_PYTHON")
    source = (
        "from typing import Optional\nfrom daytona import Daytona\n"
        "class Service:\n"
        "    def __init__(self, client: Optional[Daytona] = None):\n"
        f"        self.client{annotation} = {value}\n"
        "    def run(self): return self.client.create()\n"
    )
    # When migrating the stored value, not merely a local annotation.
    result = migrate(source)
    # Then the proven receiver moves with no review obligation.
    assert "self.client.v0.create()" in result.source
    assert not result.items
    if interpreter is None:
        pytest.skip("set DAYTONA_UPGRADE_TEST_SDK_PYTHON to a bundled SDK interpreter")
    run = subprocess.run(
        [
            interpreter,
            "-I",
            "-c",
            result.source
            + "from unittest.mock import patch\n"
            + "with patch('daytona.v0._Daytona.create', return_value=73) as create:\n"
            + "    assert Service().run() == 73\n"
            + "    assert Service(Daytona()).run() == 73\n"
            + "    assert create.call_count == 2\n",
        ],
        env={**os.environ, "DAYTONA_API_KEY": "offline-regression-key"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize(
    "store", ["client = client or Daytona()", "self.client = client or Daytona()"]
)
def test_reviews_fallback_when_injected_operand_is_unannotated(store: str) -> None:
    # Given constructor provenance but no proof of the supplied operand.
    receiver = "self.client" if store.startswith("self.") else "client"
    source = (
        "from daytona import Daytona\nclass Service:\n"
        "    def run(self, client=None):\n"
        f"        {store}\n        return {receiver}.create()\n"
    )
    # When the fallback value joins the unknown argument.
    result = migrate(source)
    # Then retain the original call and surface the existing review item.
    assert f"return {receiver}.create()" in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize("value", ["foreign or Daytona()", "client and Daytona()"])
def test_reviews_fallback_when_not_every_result_is_a_client(value: str) -> None:
    # Given a mixed or short-circuit result that can be unrelated or None.
    source = (
        "from typing import Optional\nfrom daytona import Daytona\nclass Service:\n"
        "    def __init__(self, client: Optional[Daytona], foreign):\n"
        f"        self.client = {value}\n"
        "    def run(self): return self.client.create()\n"
    )
    # When joining only supported client alternatives.
    result = migrate(source)
    # Then no guessed namespace is inserted.
    assert "return self.client.create()" in result.source
    assert [item["kind"] for item in result.items] == ["fixme"]
