# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Unsupported generator statements retain evidence without claiming exact flow."""

from __future__ import annotations

import ast

import pytest
from test_flow import migrate


@pytest.mark.parametrize(
    "body",
    [
        "try:\n        yield d\n    except ValueError:\n        yield h",
        "try:\n        yield d\n    except ValueError:\n        yield d",
        "try:\n        yield from [d]\n    except ValueError:\n        yield from [h]",
        "try:\n        value = d\n    except ValueError:\n        value = h\n    yield value",
        "try:\n        value = h\n    except ValueError:\n        value = d\n    yield value",
        "try:\n        value = d\n        yield value\n    except ValueError:\n        value = h\n        yield value",
        "try:\n        yield h\n    finally:\n        yield d",
        "try:\n        operation()\n    except ValueError:\n        yield h\n    else:\n        yield d",
        "from daytona import Daytona\n    yield Daytona()",
        "import daytona as sdk\n    yield sdk.Daytona()",
        "from daytona import Daytona\n    value = Daytona()\n    yield value",
        "try:\n        first = d\n        value = first\n    except ValueError:\n        value = h\n    yield value",
        "operation()\n    yield d",
        "if flag:\n        operation()\n        yield d\n    else:\n        yield h",
    ],
)
def test_reports_element_when_unsupported_generator_flow_can_yield_client(
    body: str,
) -> None:
    # Given known client evidence crossing statements outside exact flow inference.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        f"def values(d: Daytona, h: Client):\n    {body}\n"
        "for value in values(d, h):\n    value.create()\n"
    )
    # When migrating its yielded values without interpreting the exceptional paths.
    result = migrate(source)
    # Then possible client elements require review, never an exact-client rewrite.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


def test_reports_element_when_condition_delegates_foreign_yields() -> None:
    # Given a branch condition yielding foreign elements before a client body yield.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        "def values(d: Daytona, h: Client):\n"
        "    if (yield from [h]):\n        yield d\n"
        "for value in values(d, h):\n    value.create()\n"
    )
    # When joining the condition's delegated elements with the body's elements.
    result = migrate(source)
    # Then the foreign alternative prevents a false homogeneous-client rewrite.
    assert ast.dump(ast.parse(result.source)) == ast.dump(ast.parse(source))
    assert [item["kind"] for item in result.items] == ["fixme"]


@pytest.mark.parametrize(
    "body",
    [
        "from daytona import Daytona\n    yield h",
        "try:\n        yield h\n    except ValueError:\n        yield None",
        "try:\n        value = h\n    except ValueError:\n        value = None\n    yield value",
        "operation()\n    def nested():\n        yield d\n    yield h",
        "operation()\n    factory = lambda: (yield d)\n    yield h",
    ],
)
def test_preserves_element_when_fallback_finds_no_client_yield(body: str) -> None:
    # Given imports or nested client generators beside foreign outer yields.
    source = (
        "from daytona import Daytona\nfrom httpx import Client\n"
        f"def values(d: Daytona, h: Client):\n    {body}\n"
        "for value in values(d, h):\n    value.create()\n"
    )
    # When scanning only the current generator's possible yielded elements.
    result = migrate(source)
    # Then unused client evidence does not create a spurious diagnostic.
    assert result.source == source
    assert result.items == ()
