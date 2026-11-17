# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Desktop create options and resolved create-time metadata, not live status."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

DesktopEnvironment = Literal["xfce", "gnome", "unknown"]


class DesktopResolutionConfig(TypedDict):
    """Display dimensions; supported pairs are 1024x768, 1280x720 and 1920x1080."""

    width: int
    height: int


class DesktopConfig(TypedDict, total=False):
    """Desktop overrides for image/default creates only; ``{}`` selects XFCE 1280x720.

    Omitted fields use XFCE and 1280x720. The server returns 400 ``BAD_REQUEST`` when desktop
    support is disabled, a value or resolution is invalid, an unknown member or
    explicit JSON null is supplied, or boot resource LIMITS are too low. XFCE
    requires at least 1 CPU and 2048 MiB; GNOME requires 2 CPUs and 8192 MiB.
    These are boot limits, not guaranteed resource requests. Resolutions are
    1024x768, 1280x720 or 1920x1080. Snapshot creates and forks do not accept
    desktop overrides. ``None`` on ``create(desktop=None)`` omits the option.
    """

    environment: Literal["xfce", "gnome"]
    resolution: DesktopResolutionConfig


@dataclass(frozen=True, slots=True, kw_only=True)
class DesktopResolution:
    """Resolved desktop dimensions at creation, in pixels."""

    width: int
    height: int


@dataclass(frozen=True, slots=True, kw_only=True)
class DesktopSettings:
    """Resolved CREATE-TIME settings, not the live desktop or readiness status.

    A future environment is ``"unknown"`` with its value in ``raw_environment``.
    Guest restarts may change settings. Read live settings through
    ``sandbox.computer_use.status()`` instead.
    """

    environment: DesktopEnvironment
    raw_environment: str
    resolution: DesktopResolution


class DesktopResolutionJSON(TypedDict):
    """Resolved geometry in a serialized sandbox handle."""

    width: int
    height: int


class DesktopSettingsJSON(TypedDict):
    """Create-time desktop metadata in a serialized sandbox handle."""

    environment: DesktopEnvironment
    rawEnvironment: str
    resolution: DesktopResolutionJSON
