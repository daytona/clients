# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Remaining known Pydantic APIs receive explicit dataclass migration guidance."""

from __future__ import annotations

import pytest
from test_url_write_transactions import migrate


@pytest.mark.parametrize(
    "member",
    [
        "__fields_set__",
        "model_fields_set",
        "model_extra",
        "model_computed_fields",
        "model_post_init",
        "model_json_schema",
        "model_rebuild",
        "model_parametrized_name",
        "from_orm",
    ],
)
@pytest.mark.parametrize("receiver", ["cfg", "DaytonaConfig"])
def test_member_is_reported_when_config_has_remaining_pydantic_api(
    member: str, receiver: str
) -> None:
    # Given a proven config using metadata or a bound BaseModel method.
    source = (
        "from daytona import DaytonaConfig\ncfg = DaytonaConfig()\n"
        f"value = {receiver}.{member}\n"
    )
    # When inspecting members absent from the SDK 2.x dataclass.
    result = migrate(source)
    # Then even uncalled APIs get a manual item and a source FIXME.
    assert any(item["kind"] == "config-field" for item in result.items)
    assert "FIXME(daytona-upgrade)" in result.source


@pytest.mark.parametrize("member", ["__class__", "__dict__", "__eq__", "__repr__"])
def test_member_is_unchanged_when_dataclass_supports_object_protocol(
    member: str,
) -> None:
    # Given an object protocol shared by Pydantic and dataclass instances.
    source = f"from daytona import DaytonaConfig\ncfg = DaytonaConfig()\nvalue = cfg.{member}\n"
    # When inspecting a member that remains supported.
    result = migrate(source)
    # Then no unsupported-member diagnostic or rewrite is invented.
    assert result.source == source
    assert result.items == ()
