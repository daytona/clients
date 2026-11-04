# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""setup.py dependencies must refer to a proven call-site binding."""

from __future__ import annotations

from pathlib import Path

import pytest

from daytona_upgrade.manifests import inspect_manifest
from daytona_upgrade.models import load_mapping


@pytest.mark.parametrize(
    "body",
    [
        "if flag:\n    deps = ['daytona-sdk==0.224']\nelse:\n    deps = ['daytona-sdk==0.225']\nsetup(install_requires=deps)\n",
        "if flag:\n    deps = ['daytona-sdk==0.224']\n    setup(install_requires=deps)\nelse:\n    deps = ['daytona-sdk==0.225']\n    setup(install_requires=deps)\n",
        "deps = ['daytona-sdk==0.224']\nif flag:\n    deps = dynamic()\nsetup(install_requires=deps)\n",
        "deps = ['daytona-sdk==0.224']\ndef build():\n    setup(install_requires=deps)\ndeps = dynamic()\n",
        "deps = ['daytona-sdk==0.224']\nsetup(install_requires=[*deps, dynamic()])\n",
        "deps = ['daytona-sdk==0.224']\ndeps += dynamic()\nsetup(install_requires=deps)\n",
        "deps = ['daytona-sdk==0.224']\n(deps := dynamic())\nsetup(install_requires=deps)\n",
        "deps = ['daytona-sdk==0.224']\ndel deps\nsetup(install_requires=deps)\n",
    ],
)
def test_preserves_expression_when_setup_binding_is_uncertain(body: str) -> None:
    # Given
    source = "from setuptools import setup\n" + body
    # When
    result = inspect_manifest(Path("setup.py"), source, load_mapping())
    # Then
    assert result.source == source
    assert result.manual
    assert all(line > 0 for line, _ in result.manual)
    assert not result.changes


def test_rewrites_each_call_when_sequential_bindings_differ() -> None:
    # Given
    source = (
        "from setuptools import setup\n"
        "deps = ['daytona-sdk==0.224']\n"
        "setup(install_requires=deps)\n"
        "deps = ['daytona-sdk==0.225']\n"
        "setup(install_requires=deps)\n"
    )
    # When
    result = inspect_manifest(Path("setup.py"), source, load_mapping())
    # Then
    assert result.source == source.replace(
        "daytona-sdk==0.224", "daytona[v0-sdk]>=2,<3"
    ).replace("daytona-sdk==0.225", "daytona[v0-sdk]>=2,<3")


def test_preserves_alias_binding_when_original_name_is_reassigned() -> None:
    # Given
    source = (
        "from setuptools import setup\n"
        "deps = ['daytona-sdk==0.224']\n"
        "alias = deps\n"
        "deps = ['daytona-sdk==0.225']\n"
        "setup(install_requires=alias)\n"
    )
    # When
    result = inspect_manifest(Path("setup.py"), source, load_mapping())
    # Then
    assert result.source == source.replace(
        "daytona-sdk==0.224", "daytona[v0-sdk]>=2,<3"
    )


def test_rewrites_stable_binding_when_setup_has_main_guard() -> None:
    # Given
    source = (
        "from setuptools import setup\n"
        "deps = ['daytona-sdk==0.224']\n"
        "if __name__ == '__main__':\n"
        "    setup(install_requires=deps)\n"
    )
    # When
    result = inspect_manifest(Path("setup.py"), source, load_mapping())
    # Then
    assert result.source == source.replace(
        "daytona-sdk==0.224", "daytona[v0-sdk]>=2,<3"
    )
