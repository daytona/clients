# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Positional-only names in kwargs must not hide SDK argument evidence."""

from __future__ import annotations

import ast

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("split", [False, True])
def test_reviews_helper_when_positional_only_name_also_appears_in_kwargs(
    bound: bool, split: bool
) -> None:
    # Given: Python binds the SDK positionally and leaves the same-named kwarg alone.
    helper = (
        "class Service:\n"
        "    def run(self, client, /, **kwargs): return client.create()\n"
        if bound
        else "def run(client, /, **kwargs): return client.create()\n"
    )
    target = "Service().run" if bound else "run"
    caller = f"from daytona import Daytona\n{target}(Daytona(), client='metadata')\n"
    sources = (
        {
            "helper": helper,
            "app": f"from helper import {'Service' if bound else 'run'}\n" + caller,
        }
        if split
        else {"app": helper + caller}
    )
    mapping = load_mapping()
    name = "helper" if split else "app"
    # When
    result = transform_source(
        sources[name],
        TransformOptions(mapping),
        SourceContext(name + ".py", name, build_index(sources, mapping)),
    )
    # Then: review-only evidence reaches the helper without authorizing a rewrite.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(sources[name]))
    assert [item["kind"] for item in result.items] == ["fixme"]
