# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The leading required parameters of every public v2 method are positional-only.

Their names differ from TypeScript's in places (``write_text(path, content)``
vs ``writeText(path, text)``) and are not API: only options after them are
keywords. Adding ``/`` later would break callers, so it holds from 2.0.
"""

from __future__ import annotations

import inspect

import pytest

from daytona import v2
from daytona.v2 import _sync

_SKIP = {"__init__", "__new__", "__post_init__"}


def _public_classes() -> list[type]:
    classes: dict[str, type] = {}
    for module in (v2, _sync):
        for name in dir(module):
            value = getattr(module, name, None)
            if (
                inspect.isclass(value)
                and not name.startswith("_")
                and value.__module__.startswith("daytona.v2")
            ):
                classes[f"{value.__module__}.{value.__qualname__}"] = value
    return list(classes.values())


def _offenders() -> list[str]:
    found: list[str] = []
    for cls in _public_classes():
        for name, member in vars(cls).items():
            if name.startswith("_") or name in _SKIP:
                continue
            function = member.__func__ if isinstance(member, (staticmethod, classmethod)) else member
            if not inspect.isfunction(function):
                continue
            parameters = list(inspect.signature(function).parameters.values())
            if not isinstance(member, staticmethod) and parameters and parameters[0].name in ("self", "cls"):
                parameters = parameters[1:]
            for parameter in parameters:
                if parameter.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD:
                    continue
                if parameter.default is inspect.Parameter.empty:
                    found.append(f"{cls.__module__}.{cls.__qualname__}.{name}({parameter.name})")
    return found


def test_leading_required_parameters_are_positional_only() -> None:
    assert _offenders() == []


@pytest.mark.anyio
async def test_a_leading_parameter_by_keyword_is_refused() -> None:
    from daytona.v2.filesystem import AsyncFileSystemService

    fs = AsyncFileSystemService(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="positional-only"):
        await fs.write_text(path="/a", content="x")  # type: ignore[call-arg]
