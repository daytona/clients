# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Class annotations cannot override contradictory descriptor return evidence."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "constructor", ["requests.Session", "redis.Redis", "httpx.Client"]
)
@pytest.mark.parametrize("decorator", ["property", "cached_property"])
@pytest.mark.parametrize("receiver", ["self", "app", "App()"])
@pytest.mark.parametrize("member", ["client", "transport"])
@pytest.mark.parametrize("return_annotation", ["", " -> Daytona"])
def test_reports_when_class_annotation_conflicts_with_property_result(
    constructor: str,
    decorator: str,
    receiver: str,
    member: str,
    return_annotation: str,
) -> None:
    # Given a class annotation contradicted by a concrete third-party getter result.
    access = f"{receiver}.{member}.get('key')"
    source = (
        "from daytona import Daytona\nfrom functools import cached_property\n"
        f"import {constructor.split('.')[0]}\nclass App:\n    {member}: Daytona\n"
        f"    @{decorator}\n    def {member}(self){return_annotation}: return {constructor}()\n"
        + (
            f"    def run(self): return {access}\n"
            if receiver == "self"
            else f"app = App()\n{access}\n"
        )
    )
    # When migrating either an internal or external descriptor access.
    result = migrate(source)
    # Then the contradictory receiver stays unchanged and requires manual review.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize("decorator", ["property", "cached_property"])
@pytest.mark.parametrize("receiver", ["self", "app", "App()"])
@pytest.mark.parametrize("return_annotation", ["", " -> Daytona"])
def test_routes_when_class_annotation_agrees_with_property_result(
    decorator: str, receiver: str, return_annotation: str
) -> None:
    # Given matching annotation and concrete Daytona getter evidence.
    access = f"{receiver}.client.get('key')"
    source = (
        "from daytona import Daytona\nfrom functools import cached_property\n"
        "class App:\n    client: Daytona\n"
        f"    @{decorator}\n    def client(self){return_annotation}: return Daytona()\n"
        + (
            f"    def run(self): return {access}\n"
            if receiver == "self"
            else f"app = App()\n{access}\n"
        )
    )
    # When migrating the descriptor read.
    result = migrate(source)
    # Then the consistent proof still permits automatic routing.
    assert result.source == source.replace(access, f"{receiver}.client.v0.get('key')")
    assert not result.items
