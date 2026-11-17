# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Desktop metadata boundary mapping."""

from typing import Literal, TypeGuard

from . import _gen
from ._core import _expect_model_from_dict
from .desktop import DesktopResolution, DesktopSettings
from .types import JsonValue, _enum_values


def _known_environment(value: str) -> TypeGuard[Literal["xfce", "gnome"]]:
    return value in _enum_values(_gen.api.DesktopEnvironment)


def desktop_settings_from_wire(value: JsonValue) -> DesktopSettings | None:
    """Parse optional metadata with the standard malformed-response error."""
    if value is None:
        return None
    wire = _expect_model_from_dict(
        value, _gen.api.DesktopSettings, "sandbox desktop", _gen.api.DesktopSettings.from_dict
    )
    environment = str(wire.environment.value)
    return DesktopSettings(
        environment=environment if _known_environment(environment) else "unknown",
        raw_environment=environment,
        resolution=DesktopResolution(width=wire.resolution.width, height=wire.resolution.height),
    )
