# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Canonical keys preserve the report's ordered structural equality contract."""

from __future__ import annotations

from daytona_upgrade.models import Item, SuggestionReport
from daytona_upgrade.report_items import unique_items


def test_preserves_first_item_order_when_reports_include_nested_suggestions() -> None:
    # Given duplicates with different dictionary key orders and distinct notes.
    suggestion = SuggestionReport(
        id="client.create",
        receiver="client",
        v0="create",
        v2=None,
        category="none",
        notes=["one", "two"],
        docs="https://example.test",
    )
    first = Item(
        file="app.py",
        line=3,
        column=1,
        kind="suggestion",
        message="review",
        suggestion=suggestion,
    )
    duplicate = Item(
        message="review",
        kind="suggestion",
        column=1,
        line=3,
        file="app.py",
        suggestion={**suggestion},
    )
    different = Item(**{**first, "suggestion": {**suggestion, "notes": ["two", "one"]}})
    last = Item(file="app.py", line=7, column=1, kind="env-api-url", message="review")
    inputs = [first, duplicate, different, last, first, different]

    # When canonical de-duplication processes the reports in encounter order.
    items = list(unique_items(inputs))

    # Then structural duplicates collapse without sorting items or list payloads.
    assert items == [first, different, last]
    assert items[0] is first
