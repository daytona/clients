# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Environment path proofs must not multiply ordinary chained-call resolution."""

from __future__ import annotations

import ast

import pytest

from daytona_upgrade.environment_paths import EnvironmentResolver
from daytona_upgrade.models import load_mapping
from daytona_upgrade.resolution import ProjectIndex, Resolver


def test_bounds_resolution_work_when_environment_scans_chained_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an ordinary SDK call chain and the same bindings in both resolvers.
    expression = ast.parse(
        'client.create().process.exec("command").result().strip()', mode="eval"
    ).body
    mapping = load_mapping()
    index = ProjectIndex({})
    env = {"client": "client"}
    calls = 0
    infer = Resolver.infer

    def count_inference(self: Resolver, node: ast.AST, env: dict[str, str]) -> str:
        nonlocal calls
        calls += 1
        return infer(self, node, env)

    monkeypatch.setattr(Resolver, "infer", count_inference)
    expected = Resolver(mapping, index).infer(expression, env)
    baseline = calls
    calls = 0

    # When environment preflight resolves the same non-path expression.
    actual = EnvironmentResolver(mapping, index).infer(expression, env)

    # Then extra path proofs preserve the result without amplifying recursion.
    assert actual == expected
    assert calls <= baseline * 2, (calls, baseline)
