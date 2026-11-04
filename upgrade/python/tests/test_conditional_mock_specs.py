# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Conditional and assignment specs retain review-only Daytona links."""

from __future__ import annotations

import pytest
from test_url_write_transactions import migrate


@pytest.mark.parametrize(
    "spec",
    [
        "LinkedClient if flag else Other",
        "Other if flag else LinkedClient",
        "(S := LinkedClient)",
        "(S := LinkedClient if flag else Other)",
        "LinkedClient() if flag else Other",
    ],
)
def test_reviews_when_spec_expression_contains_replaced_daytona_class(
    spec: str,
) -> None:
    # Given a historical SDK class link but no current class proof.
    source = (
        "from unittest.mock import MagicMock\nfrom daytona import Daytona\n"
        "LinkedClient = Daytona\nLinkedClient = replacement\n"
        f"client = MagicMock(spec=({spec}))\nclient.create()\n"
    )

    # When mock inference inspects the spec's possible values.
    result = migrate(source)

    # Then only review is added; an unknown shape never gains rewrite authority.
    assert result.source == source
    assert [item["kind"] for item in result.items] == ["mock-target"]
