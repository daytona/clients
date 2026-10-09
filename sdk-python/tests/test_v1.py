# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib

import pytest

import daytona


@pytest.mark.parametrize("name", daytona.__all__)
def test_v1_reexports_every_public_name(name: str):
    v1 = importlib.import_module("daytona.v1")
    assert getattr(v1, name) is getattr(daytona, name)


def test_v1_all_matches_root_all():
    from daytona import v1

    assert v1.__all__ == daytona.__all__


def test_v1_from_import():
    from daytona.v1 import AsyncDaytona, Daytona, DaytonaConfig, Sandbox

    assert Daytona is daytona.Daytona
    assert AsyncDaytona is daytona.AsyncDaytona
    assert Sandbox is daytona.Sandbox
    assert DaytonaConfig is daytona.DaytonaConfig


def test_v1_is_reachable_as_package_attribute():
    assert daytona.v1 is importlib.import_module("daytona.v1")


def test_v1_unknown_attribute_raises():
    v1 = importlib.import_module("daytona.v1")
    with pytest.raises(AttributeError):
        _ = v1.NotAPublicName
