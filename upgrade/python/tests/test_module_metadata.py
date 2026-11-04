# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""SDK module metadata belongs to the root module, not its v0 namespace."""

from __future__ import annotations

import pytest
from test_flow import migrate


@pytest.mark.parametrize("module", ["daytona", "daytona_sdk"])
@pytest.mark.parametrize("alias", ["", " as sdk"])
@pytest.mark.parametrize(
    "member", ["__file__", "__name__", "__dict__", "__path__", "__custom__"]
)
def test_metadata_stays_on_root_when_sdk_module_is_renamed(
    module: str, alias: str, member: str
) -> None:
    # Given module metadata accessed directly or through an import alias.
    receiver = "sdk" if alias else module
    source = f"import {module}{alias}\nvalue = {receiver}.{member}\n"
    expected = (
        f"import daytona{alias}\nvalue = {'sdk' if alias else 'daytona'}.{member}\n"
    )
    # When migrating imports and exported attributes.
    result = migrate(source)
    # Then only the package rename occurs, without a private-export diagnostic.
    assert result.source == expected
    assert result.items == ()
