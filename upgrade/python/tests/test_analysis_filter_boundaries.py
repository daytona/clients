# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Implicit project links must enter flow analysis even without SDK imports."""

from __future__ import annotations

import ast

import pytest

from daytona_upgrade.flow import Flow, build_index
from daytona_upgrade.models import load_mapping


@pytest.mark.parametrize(
    "source",
    [
        "import pytest\n@pytest.fixture\ndef resource(): return unknown\n",
        "import pytest\n@pytest.fixture()\ndef resource(): return unknown\n",
        "from pytest import fixture\n@fixture\ndef resource(): return unknown\n",
        "from pytest import fixture\n@fixture()\ndef resource(): return unknown\n",
        "CONFIG = unknown\n__all__ = computed_names\n",
        "from missing_provider import *\n",
    ],
)
def test_flow_runs_when_module_has_an_implicit_project_link(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    # Given an implicit provider disconnected from ordinary SDK imports.
    sources = {
        "app": "from daytona import Daytona\nclient = Daytona()\n",
        "support.provider": source,
    }
    observed: list[str] = []
    analyze = Flow.analyze

    def track(self: Flow, text: str, module: ast.Module | None = None) -> None:
        observed.append(text)
        analyze(self, text, module)

    monkeypatch.setattr(Flow, "analyze", track)
    # When the project index chooses modules for flow analysis.
    build_index(sources, load_mapping())
    # Then the provider is analyzed rather than silently losing its evidence.
    assert source in observed


def test_plugin_is_analyzed_when_another_module_declares_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an SDK consumer declaring a distinct, otherwise unseeded plugin.
    plugin = "resource = unknown\n"
    sources = {
        "app": "from daytona import Daytona\npytest_plugins = ['support.plugin']\n",
        "support.plugin": plugin,
    }
    observed: list[str] = []
    analyze = Flow.analyze

    def track(self: Flow, text: str, module: ast.Module | None = None) -> None:
        observed.append(text)
        analyze(self, text, module)

    monkeypatch.setattr(Flow, "analyze", track)
    # When following the plugin declaration rather than seeding the provider.
    build_index(sources, load_mapping())
    # Then the distinct plugin participates in flow analysis.
    assert plugin in observed


def test_conftest_is_analyzed_when_sdk_module_is_outside_its_directory() -> None:
    # Given a conftest outside the SDK module's ancestor chain.
    source = "value = unknown\n"
    sources = {
        "app": "from daytona import Daytona\nclient = Daytona()\n",
        "support.conftest": source,
    }
    # When building the filtered project index.
    index = build_index(sources, load_mapping())
    # Then the conftest remains eligible to supply implicit fixture evidence.
    assert ("support.conftest", source) not in index.skipped_sources
