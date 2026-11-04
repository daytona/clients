# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Call arguments are review evidence, never an inferred parameter annotation."""

from __future__ import annotations

import ast

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    "call", ["run_task(Daytona(), 'hi')", "run_task(cmd='hi', client=Daytona())"]
)
@pytest.mark.parametrize("split", [False, True])
def test_reviews_helper_when_call_argument_is_client(call: str, split: bool) -> None:
    # Given a direct constructor flowing into an unannotated helper.
    helper = "def run_task(client, cmd):\n    sandbox = client.create()\n    client.delete(sandbox)\n"
    caller = "from daytona import Daytona\n" + call + "\n"
    sources = (
        {"helper": helper, "app": "from helper import run_task\n" + caller}
        if split
        else {"app": helper + caller}
    )
    mapping = load_mapping()
    index = build_index(sources, mapping)
    name = "helper" if split else "app"
    # When the actual argument establishes a link, but not an all-callers proof.
    result = transform_source(
        sources[name],
        TransformOptions(mapping),
        SourceContext(name + ".py", name, index),
    )
    # Then both moved accesses remain unchanged and require review.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(sources[name]))
    assert [item["kind"] for item in result.items] == ["fixme", "fixme"]


@pytest.mark.parametrize("replacement", ["", "    client = foreign\n"])
def test_propagates_review_when_annotated_consumer_forwards_client(
    replacement: str,
) -> None:
    # Given multiple forwarding levels and a possible explicit replacement.
    source = (
        "from daytona import Daytona\n"
        "def launch(client):\n" + replacement + "    return client.create()\n"
        "def middle(value): return launch(value)\n"
        "def consume(client: Daytona): return middle(client)\n"
    )
    mapping = load_mapping()
    # When links reach a fixed point without becoming rewrite proofs.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then only a still-linked receiver is reviewed, and none is rewritten.
    assert "client.v0.create" not in ast.unparse(ast.parse(result.source))
    assert bool(result.items) is (not replacement)


def test_leaves_unrelated_parameter_when_other_slot_receives_client() -> None:
    # Given a generic client parameter unrelated to the SDK argument.
    source = "from daytona import Daytona\ndef run(client, sdk): return client.create()\nrun(foreign, Daytona())\n"
    mapping = load_mapping()
    # When linking parameters by position rather than spelling.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then no diagnostic or rewrite touches the unrelated receiver.
    assert result.source == source
    assert result.items == ()


def test_reviews_method_when_annotated_consumer_forwards_client() -> None:
    # Given a first-party method reached through its bound instance parameter.
    source = "from daytona import Daytona\nclass Service:\n    def launch(self, client): return client.create()\n    def consume(self, client: Daytona): return self.launch(client)\n"
    mapping = load_mapping()
    # When linking the method slot without treating self as the supplied client.
    result = transform_source(
        source,
        TransformOptions(mapping),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then the helper's member is preserved with a review obligation.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]
