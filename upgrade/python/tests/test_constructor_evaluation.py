# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Constructor warnings follow eager evaluation within exception boundaries."""

from __future__ import annotations

import sys
from textwrap import indent

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "argument",
    ["(Failure := DaytonaError)", "config=(Failure := DaytonaError)"],
)
def test_reports_when_argument_binds_the_caught_error_alias(argument: str) -> None:
    # Given an unrelated alias replaced while evaluating constructor arguments.
    source = f"from daytona import Daytona, DaytonaError\nFailure = ValueError\ntry:\n    client = Daytona({argument})\nexcept Failure:\n    pass\n"
    # When upgrading the construction.
    result = migrate(source)
    # Then the handler's evaluated error identity requires review.
    assert [item["line"] for item in result.items if item["kind"] == "fixme"] == [4]


def test_preserves_when_argument_replaces_the_caught_error_alias() -> None:
    # Given a matching alias replaced by an unrelated error before the call.
    source = "from daytona import Daytona, DaytonaError\nFailure = DaytonaError\ntry:\n    client = Daytona(Failure := ValueError)\nexcept Failure:\n    pass\n"
    # When upgrading the construction.
    result = migrate(source)
    # Then the old alias cannot invent a matching handler.
    assert not [item for item in result.items if item["kind"] == "fixme"]


def test_reports_when_argument_rebinds_the_constructor_name() -> None:
    # Given a callee loaded before an argument replaces its binding.
    source = "from daytona import Daytona, DaytonaError\ntry:\n    client = Daytona(Daytona := ValueError)\nexcept DaytonaError:\n    pass\n"
    # When upgrading the construction.
    result = migrate(source)
    # Then the already-loaded SDK constructor still requires review.
    assert [item["line"] for item in result.items if item["kind"] == "fixme"] == [3]


@pytest.mark.skipif(sys.version_info < (3, 11), reason="except* requires Python 3.11")
def test_reports_when_exception_group_handler_protects_the_constructor() -> None:
    # Given a constructor inside an except* boundary, parsed only on Python 3.11+.
    source = "from daytona import Daytona, DaytonaError\ntry:\n    client = Daytona()\nexcept* DaytonaError:\n    pass\n"
    # When upgrading the construction.
    result = migrate(source)
    # Then grouped handlers get the same located warning as ordinary handlers.
    assert [(item["kind"], item["line"]) for item in result.items] == [("fixme", 3)]


@pytest.mark.parametrize(
    "definition",
    [
        "def factory(client=Daytona()):\n    pass",
        "def factory(*, client=Daytona()):\n    pass",
        "async def factory(client=Daytona()):\n    pass",
        "@decorate(Daytona())\ndef factory():\n    pass",
        "def factory(client: Daytona()):\n    pass",
        "def factory() -> Daytona():\n    pass",
        "factory = lambda client=Daytona(): None",
        "class Factory(make_base(Daytona())):\n    pass",
        "class Factory(metaclass=make_meta(Daytona())):\n    pass",
        "@decorate(Daytona())\nclass Factory:\n    pass",
        "class Factory:\n    client = Daytona()",
        "class Factory:\n    def factory(client=Daytona()):\n        pass",
    ],
)
def test_reports_when_definition_evaluates_a_constructor_inside_try(
    definition: str,
) -> None:
    # Given construction in an eager part of a definition, not a deferred body.
    source = (
        "from daytona import Daytona, DaytonaError\ntry:\n"
        + indent(definition, "    ")
        + "\nexcept DaytonaError:\n    pass\n"
    )
    # When upgrading the definition.
    result = migrate(source)
    # Then the enclosing handler's credential boundary requires review.
    assert [item["kind"] for item in result.items] == ["fixme"]
    assert "# FIXME(daytona-upgrade):" in result.source


@pytest.mark.parametrize(
    "definition",
    [
        "def factory():\n    return Daytona()",
        "async def factory():\n    return Daytona()",
        "factory = lambda: Daytona()",
        "class Factory:\n    def factory():\n        return Daytona()",
        "def factory():\n    def nested(client=Daytona()):\n        pass",
        "factory = lambda: (lambda client=Daytona(): None)",
    ],
)
def test_preserves_when_constructor_is_in_a_deferred_body(definition: str) -> None:
    # Given construction deferred beyond the surrounding try's execution.
    source = (
        "from daytona import Daytona, DaytonaError\ntry:\n"
        + indent(definition, "    ")
        + "\nexcept DaytonaError:\n    pass\n"
    )
    # When upgrading the definition.
    result = migrate(source)
    # Then definition-time scanning does not cross the deferred body boundary.
    assert not result.items
