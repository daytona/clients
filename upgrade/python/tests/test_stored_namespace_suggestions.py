# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Stored v0 clients never receive suggestions for nonexistent .v2 attributes."""

from __future__ import annotations

import ast

import pytest

from daytona_upgrade.flow import SourceContext, build_index
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    "body",
    [
        "api = Daytona()\nlegacy = api.v0\nlegacy.create()",
        "api = Daytona()\nfirst = api.v0\nlegacy = first\nlegacy.create()",
        "api = Daytona()\nlegacy: Daytona = api.v0\nlegacy.create()",
        "api = Daytona()\n(legacy := api.v0)\nlegacy.create()",
        "root_client = Daytona()\nlegacy = root_client.v0\nlegacy.create()",
    ],
)
def test_suggestion_uses_root_when_v0_alias_has_a_live_owner(body: str) -> None:
    # Given a stored v0 alias with a still-live root binding.
    source = "from daytona import Daytona\n" + body + "\n"
    mapping = load_mapping()
    # When requesting comment-only guidance.
    result = transform_source(
        source,
        TransformOptions(mapping, "suggest-v2"),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then the generated code uses the root, never the bundled legacy client.
    assert [
        item["suggestion"]["v2"] for item in result.items if "suggestion" in item
    ] == [
        ("root_client" if body.startswith("root_client") else "api")
        + ".v2.create(image, region=region)"
    ]
    assert all(item["kind"] != "fixme" for item in result.items)
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


def test_suggestions_are_removable_when_v0_owner_is_unknown() -> None:
    # Given a stored legacy client whose root was never retained.
    source = "from daytona import Daytona\nlegacy = Daytona().v0\nlegacy.create()\n"
    mapping = load_mapping()
    context = SourceContext("app.py", "app", build_index({"app": source}, mapping))
    suggested = transform_source(
        source, TransformOptions(mapping, "suggest-v2"), context
    ).source
    # When removing the generated suggestion block.
    removed = transform_source(
        suggested, TransformOptions(mapping, "remove-suggestions"), context
    )
    # Then manual owner guidance is a removable suggestion, not persistent code.
    assert removed.source == source


@pytest.mark.parametrize(
    "body",
    [
        "legacy = Daytona().v0\nlegacy.create()",
        "api = Daytona()\nlegacy = api.v0\napi = Daytona()\nlegacy.create()",
        "api = Daytona()\nlegacy = api.v0\nif flag:\n    api = Daytona()\nlegacy.create()",
    ],
)
def test_suggestion_is_manual_when_stored_v0_owner_is_unavailable(body: str) -> None:
    # Given a stored legacy client without a reliable live root binding.
    source = "from daytona import Daytona\n" + body + "\n"
    mapping = load_mapping()
    # When requesting guidance.
    result = transform_source(
        source,
        TransformOptions(mapping, "suggest-v2"),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then guidance is explicitly manual and never attributes .v2 to legacy.
    items = [item for item in result.items if "suggestion" in item]
    assert len(items) == 1
    assert items[0]["kind"] == "fixme"
    assert items[0]["suggestion"]["v2"] != "legacy.v2.create(image, region=region)"
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize("owned", [True, False])
def test_service_suggestion_when_receiver_is_a_stored_v0_client(owned: bool) -> None:
    # Given a service accessed through a v0 alias, with or without a retained root.
    source = (
        "from daytona import Daytona\n"
        + ("api = Daytona()\nlegacy = api.v0\n" if owned else "legacy = Daytona().v0\n")
        + "legacy.snapshot.list()\n"
    )
    mapping = load_mapping()
    # When requesting guidance for the client service.
    result = transform_source(
        source,
        TransformOptions(mapping, "suggest-v2"),
        SourceContext("app.py", "app", build_index({"app": source}, mapping)),
    )
    # Then the v2 path starts at the owning root, or requires manual owner selection.
    items = [item for item in result.items if "suggestion" in item]
    assert items[0]["kind"] == ("suggestion" if owned else "fixme")
    assert items[0]["suggestion"]["v2"] == (
        ("api" if owned else "root_client")
        + ".v2.images.list(cursor=cursor, limit=limit, sandbox_id=source_sandbox_id)"
    )
