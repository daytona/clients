# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Constructor error handling must survive the root client's lazy v0 boundary."""

from __future__ import annotations

from textwrap import dedent

import pytest
from test_flow import migrate

from daytona_upgrade.models import SourceResult


@pytest.mark.parametrize(
    "handler",
    [
        "",
        "DaytonaError",
        "(ValueError, DaytonaAuthenticationError)",
        # The two 0.x root aliases 2.0 dropped name shared categories too.
        "DaytonaValidationError",
        "(KeyError, DaytonaAuthorizationError)",
        "Exception",
        "BaseException",
    ],
)
@pytest.mark.parametrize(
    "imports, constructor",
    [
        ("from daytona import Daytona", "Daytona"),
        ("from daytona import AsyncDaytona", "AsyncDaytona"),
        ("from daytona import Daytona as Factory", "Factory"),
        ("import daytona", "daytona.Daytona"),
        ("import daytona as sdk", "sdk.AsyncDaytona"),
    ],
)
def test_manual_item_when_constructor_has_a_matching_handler(
    handler: str, imports: str, constructor: str
) -> None:
    # Given a proven root constructor protected by an applicable handler.
    source = f"{imports}\nfrom daytona import DaytonaError, DaytonaAuthenticationError, DaytonaValidationError, DaytonaAuthorizationError\ntry:\n    integration = {constructor}()\nexcept {handler}:\n    integration = None\n"
    # When upgrading the integration.
    result = migrate(source)
    # Then review guidance is located at the construction, using its assigned name.
    assert len(result.items) == 1
    item = result.items[0]
    assert (item["kind"], item["line"]) == ("fixme", 4)
    assert "integration.v0" in item["message"]
    assert "# FIXME(daytona-upgrade):" in result.source


@pytest.mark.parametrize(
    "body",
    [
        "client = Daytona()",
        "try:\n    client = Daytona()\nexcept ValueError:\n    pass",
        "try:\n    def factory():\n        return Daytona()\nexcept DaytonaError:\n    pass",
        "try:\n    factory = lambda: Daytona()\nexcept DaytonaError:\n    pass",
        "try:\n    pass\nexcept DaytonaError:\n    client = Daytona()",
        "try:\n    pass\nexcept DaytonaError:\n    pass\nelse:\n    client = Daytona()",
        "try:\n    pass\nexcept DaytonaError:\n    pass\nfinally:\n    client = Daytona()",
        "def factory(Daytona):\n    try:\n        client = Daytona()\n    except DaytonaError:\n        pass",
    ],
)
def test_preserves_construction_when_matching_try_does_not_protect_it(
    body: str,
) -> None:
    # Given unrelated handlers, deferred construction, or an unproven constructor.
    source = "from daytona import Daytona, DaytonaError\n" + body + "\n"
    # When upgrading it.
    result = migrate(source)
    # Then no lazy-constructor review is invented.
    assert not result.items
    assert "FIXME(daytona-upgrade)" not in result.source


def returned_client() -> SourceResult:
    return migrate(
        dedent("""\
        from daytona import Daytona, DaytonaError
        def optional():
            try:
                return Daytona()
            except DaytonaError:
                return None
        """)
    )


def test_fallback_receiver_when_constructor_is_returned() -> None:
    # Given an integration returning the constructor directly.
    # When upgrading it.
    result = returned_client()
    # Then the diagnostic names no invented receiver.
    assert len(result.items) == 1
    assert "access .v0 on this client inside the try" in result.items[0]["message"]


def test_idempotent_when_constructor_review_already_exists() -> None:
    # Given a migrated constructor with its review comment.
    first = returned_client()
    # When upgrading again.
    second = migrate(first.source)
    # Then the manual item persists without duplicating comments.
    assert second.source == first.source
    assert len(second.items) == 1


@pytest.mark.parametrize(
    "imports, handler",
    [
        ("from daytona.v0 import DaytonaError", "DaytonaError"),
        ("from daytona import DaytonaError as Failure", "Failure"),
        ("import daytona as sdk", "sdk.DaytonaError"),
        ("import daytona.v0 as sdk", "sdk.DaytonaAuthenticationError"),
        ("import daytona.v0", "daytona.v0.DaytonaError"),
        ("import builtins", "builtins.Exception"),
        ("from builtins import BaseException as Failure", "Failure"),
    ],
)
def test_reports_when_handler_uses_a_proven_error_alias(
    imports: str, handler: str
) -> None:
    # Given shared errors imported from either SDK generation or a namespace.
    source = f"from daytona import Daytona\n{imports}\ntry:\n    client = Daytona()\nexcept {handler}:\n    pass\n"
    # When upgrading it.
    result = migrate(source)
    # Then the actual imported error identity triggers manual review.
    assert len(result.items) == 1
    assert result.items[0]["kind"] == "fixme"


@pytest.mark.parametrize("target", ["integration: Daytona", "self.integration"])
def test_names_receiver_when_assignment_is_annotated_or_an_attribute(
    target: str,
) -> None:
    # Given a constructor nested in a conditional inside the protected body.
    source = f"from daytona import Daytona\ntry:\n    if enabled:\n        {target} = Daytona()\nexcept Exception:\n    pass\n"
    # When upgrading it.
    result = migrate(source)
    # Then the diagnostic points at the assigned receiver rather than a placeholder.
    receiver = target.split(":")[0]
    assert len(result.items) == 1
    assert f"{receiver}.v0" in result.items[0]["message"]


def test_reports_when_handler_import_is_evaluated_before_construction() -> None:
    # Given an error import in the try body, guaranteed to precede this constructor.
    source = "from daytona import Daytona\ntry:\n    from daytona import DaytonaError as Failure\n    client = Daytona()\nexcept Failure:\n    pass\n"
    # When upgrading the protected constructor.
    result = migrate(source)
    # Then prefix uncertainty from the import itself does not hide the risk.
    assert len(result.items) == 1
    assert result.items[0]["line"] == 4


def test_preserves_construction_when_builtin_handler_name_is_rebound() -> None:
    # Given Exception shadowed by an unrelated error.
    source = "from daytona import Daytona\nException = ValueError\ntry:\n    client = Daytona()\nexcept Exception:\n    pass\n"
    # When upgrading it.
    result = migrate(source)
    # Then a spelling alone does not create a matching handler.
    assert not result.items


def test_uses_fallback_when_constructor_is_wrapped_in_a_container() -> None:
    # Given a collection assignment, not a name bound to the constructed client.
    source = "from daytona import Daytona\ntry:\n    clients = [Daytona()]\nexcept Exception:\n    pass\n"
    # When upgrading it.
    result = migrate(source)
    # Then guidance does not recommend accessing a list's nonexistent v0 property.
    assert len(result.items) == 1
    assert "access .v0 on this client inside the try" in result.items[0]["message"]
