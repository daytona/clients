# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Receiver and annotation boundaries preserve source identity."""

from __future__ import annotations

import pytest

from daytona_upgrade.flow import SourceContext
from daytona_upgrade.models import TransformOptions, load_mapping
from daytona_upgrade.resolution import ProjectIndex
from daytona_upgrade.transform import transform_source


@pytest.mark.parametrize(
    "annotation", ["Daytona", '"Daytona"', "Optional[Daytona]", "AsyncDaytona"]
)
def test_routes_client_when_assignment_annotation_resolves_to_sdk(
    annotation: str,
) -> None:
    # Given a typed client supplied by an external module, without a useful name.
    source = f"from daytona import Daytona, AsyncDaytona\nfrom typing import Optional\nfrom mycorp import deps\nc: {annotation} = deps.shared\nc.create()\n"
    # When migrating the source.
    result = transform_source(
        source,
        TransformOptions(load_mapping()),
        SourceContext("app.py", "app", ProjectIndex({})),
    )
    # Then annotation evidence routes the call, not the external value's spelling.
    assert "c.v0.create()" in result.source


@pytest.mark.parametrize(
    "receiver", ["settings.DAYTONA_CLIENT", "deps.daytona", "daytona_client"]
)
def test_reports_client_when_external_attribute_has_daytona_name(receiver: str) -> None:
    # Given an untyped external client, including a direct imported value.
    source = f"from django.conf import settings\nfrom mycorp import deps, daytona_client\n{receiver}.create()\n"
    # When migrating without inspecting external packages.
    result = transform_source(
        source,
        TransformOptions(load_mapping()),
        SourceContext("app.py", "app", ProjectIndex({})),
    )
    # Then the unknown receiver requires review instead of silently surviving.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert f"{receiver}.create()" in result.source


@pytest.mark.parametrize("member", load_mapping().moved)
@pytest.mark.parametrize(
    "syntax",
    ["import daytona.common.{member}", "from daytona.common.{member} import Thing"],
)
def test_avoids_receiver_fixme_when_module_path_contains_moved_name(
    member: str, syntax: str
) -> None:
    # Given a private module whose path overlaps a moved client member.
    source = syntax.format(member=member) + "\n"
    # When migrating the import path.
    result = transform_source(
        source,
        TransformOptions(load_mapping()),
        SourceContext("app.py", "app", ProjectIndex({})),
    )
    # Then the path moves once, with only its private-import diagnostic.
    assert "daytona.v0.common." + member in result.source
    assert [item["kind"] for item in result.items] == ["private-import"]


@pytest.mark.parametrize(
    "annotation", ['"daytona.Sandbox"', 'Optional["daytona.Sandbox"]', '"sdk.Image"']
)
def test_moves_type_when_forward_reference_uses_sdk_module(annotation: str) -> None:
    # Given a forward reference in a real annotation.
    source = f"import daytona\nimport daytona as sdk\nfrom typing import Optional\ndef f(x: {annotation}): pass\n"
    # When migrating the reference.
    result = transform_source(
        source,
        TransformOptions(load_mapping()),
        SourceContext("app.py", "app", ProjectIndex({})),
    )
    # Then the string expression follows the same namespace routing as live types.
    assert ".v0." in result.source
    assert result.items == ()


def test_reports_annotation_when_forward_reference_cannot_be_parsed() -> None:
    # Given an invalid forward reference mentioning a moved type.
    source = 'import daytona\ndef f(x: "daytona.Sandbox["): pass\n'
    # When migrating instead of evaluating user annotations.
    result = transform_source(
        source,
        TransformOptions(load_mapping()),
        SourceContext("app.py", "app", ProjectIndex({})),
    )
    # Then review is required and the original string is preserved.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert '"daytona.Sandbox["' in result.source


def test_reports_receiver_when_client_is_in_dict_literal() -> None:
    # Given a dictionary whose values include an SDK client.
    source = 'from daytona import Daytona\nregistry = {"a": Daytona()}\nregistry["a"].create()\n'
    # When migrating without proving dictionary key identities.
    result = transform_source(
        source,
        TransformOptions(load_mapping()),
        SourceContext("app.py", "app", ProjectIndex({})),
    )
    # Then a review item guards the unproven lookup.
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize("comment", ["# noqa: F401", "# shared types"])
def test_preserves_comment_when_import_is_split(comment: str) -> None:
    # Given a mixed import with a trailing suppression or comment.
    source = f"from daytona import Daytona, Image  {comment}\n"
    # When splitting root and resource imports.
    result = transform_source(
        source,
        TransformOptions(load_mapping()),
        SourceContext("app.py", "app", ProjectIndex({})),
    )
    # Then both resulting lines retain the original trailing comment.
    assert result.source.splitlines()[0].endswith(comment)
    assert result.source.count(comment) == 2
