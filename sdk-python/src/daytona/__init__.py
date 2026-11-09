# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Daytona Python SDK.

The package root holds the clients, their configuration and the error
hierarchy both API versions share (``except daytona.DaytonaNotFoundError``
catches it from ``.v0`` and ``.v2``); every operation goes through a version
namespace::

    from daytona import Daytona

    d = Daytona()          # DAYTONA_API_KEY serves both namespaces
    d.v2.create("python:3.12")
    d.v0.list()            # needs the v0-sdk extra: pip install "daytona[v0-sdk]"

Resource types (``Sandbox``, ``Image``, ...) and version-specific errors live
in ``daytona.v0`` and ``daytona.v2``. The clients exist only here: ``Daytona`` is
synchronous and ``AsyncDaytona`` asynchronous, and each namespace client is reached
through them (``Daytona().v2``, ``AsyncDaytona().v0``). ``DaytonaV2Client`` and
the other ``…Client`` names type a stored namespace client; they cannot build one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# ruff: noqa: I001

from ._client import V0_API_URL as V0_API_URL
from ._client import _V0_EXTRA_HINT
from ._client import AsyncDaytona as AsyncDaytona
from ._client import Daytona as Daytona
from ._client import DaytonaConfig as DaytonaConfig
from ._client import AsyncDaytonaV2Client as AsyncDaytonaV2Client  # annotation-only
from ._client import DaytonaV2Client as DaytonaV2Client  # annotation-only
from .v2.daytona import DEFAULT_V2_API_URL as DEFAULT_V2_API_URL

# The error hierarchy shared by both namespaces: these catch v0 and v2 errors
# alike, category for category, for API errors. The bundled 0.x v0 raises its
# plain DaytonaError for transport failures (refused connection, timeout), so
# those match DaytonaError only. Version-specific errors live in
# daytona.v0 / daytona.v2.
from ._errors import (
    DaytonaError as DaytonaError,
    DaytonaBadRequestError as DaytonaBadRequestError,
    DaytonaAuthenticationError as DaytonaAuthenticationError,
    DaytonaForbiddenError as DaytonaForbiddenError,
    DaytonaNotFoundError as DaytonaNotFoundError,
    DaytonaTimeoutError as DaytonaTimeoutError,
    DaytonaConflictError as DaytonaConflictError,
    DaytonaGoneError as DaytonaGoneError,
    DaytonaUnprocessableEntityError as DaytonaUnprocessableEntityError,
    DaytonaRateLimitError as DaytonaRateLimitError,
    DaytonaInternalServerError as DaytonaInternalServerError,
    DaytonaServiceUnavailableError as DaytonaServiceUnavailableError,
    DaytonaConnectionError as DaytonaConnectionError,
)

__all__ = [
    "DEFAULT_V2_API_URL",
    "V0_API_URL",
    "AsyncDaytona",
    "AsyncDaytonaV2Client",
    "Daytona",
    "DaytonaAuthenticationError",
    "DaytonaBadRequestError",
    "DaytonaConfig",
    "DaytonaConflictError",
    "DaytonaConnectionError",
    "DaytonaError",
    "DaytonaForbiddenError",
    "DaytonaGoneError",
    "DaytonaInternalServerError",
    "DaytonaNotFoundError",
    "DaytonaRateLimitError",
    "DaytonaServiceUnavailableError",
    "DaytonaTimeoutError",
    "DaytonaUnprocessableEntityError",
    "DaytonaV2Client",
]

if TYPE_CHECKING:
    from . import v0 as v0
    from . import v2 as v2

    # The v0 client classes, for annotations (bundled, so resolved lazily at
    # runtime by __getattr__ below and kept out of __all__: `import *` must not
    # need the v0-sdk extra).
    from ._client import AsyncDaytonaV0Client as AsyncDaytonaV0Client
    from ._client import DaytonaV0Client as DaytonaV0Client


# Defined only at runtime: a module-level __getattr__ makes type checkers treat
# every attribute as present, which would hide `from daytona import Sandbox`
# from mypy / pyright.
if not TYPE_CHECKING:
    # The two 0.x root error aliases 2.0 dropped, each with the shared category
    # it named (the migration tool's `rootAliasCategories`, which a test pins).
    _FORMER_ROOT_ALIASES = {
        "DaytonaValidationError": "DaytonaBadRequestError",
        "DaytonaAuthorizationError": "DaytonaForbiddenError",
    }

    def _moved_to(name: str) -> tuple[bool, bool]:
        """Whether a pre-2.0 root name now lives in ``daytona.v0`` / ``daytona.v2``.

        ``(False, False)`` means the root never exported it: a typo or a probe.
        """
        from ._v0_root_names import V0_ROOT_NAMES
        from .v2._exports import ROOT_EXPORTS

        return name in V0_ROOT_NAMES, name in ROOT_EXPORTS

    def __dir__() -> list[str]:
        """List the root's names plus the ``v0`` / ``v2`` namespaces (``v0`` is lazy,
        and listed only when this build carries the bundle, so introspection such
        as ``inspect.getmembers`` works in source checkouts)."""
        import importlib.util

        names = {*globals(), "v2"}
        if importlib.util.find_spec(f"{__name__}.v0") is not None:
            names.add("v0")
        return sorted(names)

    def __getattr__(name: str) -> object:
        """Resolve ``daytona.v0`` lazily and guide imports SDK 2.0 moved off the root.

        ``v0`` is a submodule that is imported on first use (it needs the
        ``v0-sdk`` extra). CPython probes ``hasattr(daytona, "v0")`` before it
        imports a submodule for ``from daytona import v0``, so the module
        ``__getattr__`` imports it here.

        A name the root exported before 2.0 (``from daytona import Sandbox``
        worked in the 0.x SDK and in the 2.0 alphas) raises an ``ImportError``
        that says which namespace to import it from. Deliberately an
        ``ImportError``: for ``from daytona import X`` CPython replaces an
        ``AttributeError`` from a module ``__getattr__`` with its generic
        "cannot import name" message, dropping the guidance. The cost is that
        ``hasattr(daytona, "Sandbox")`` raises instead of returning ``False``.

        Every other name, including typos and private names, raises a plain
        ``AttributeError``, so ``hasattr`` / ``getattr(daytona, x, default)``
        behave normally for them.
        """
        if name in ("DaytonaV0Client", "AsyncDaytonaV0Client"):
            # The types of `Daytona().v0` / `AsyncDaytona().v0`, for
            # annotations only (they cannot build a client). They need the
            # bundle and the v0-sdk extra: load it now so a missing extra is
            # reported here, not at the first isinstance check.
            from . import _client

            _client._load_v0(f"_{name[: -len('V0Client')]}")
            return getattr(_client, name)
        if name == "v0":
            import importlib

            try:
                module = importlib.import_module(f"{__name__}.v0")
            except ModuleNotFoundError as exc:
                if exc.name in (f"{__name__}.v0", None):
                    raise ImportError(_V0_EXTRA_HINT, name=f"{__name__}.v0") from exc
                raise ImportError(
                    f"{_V0_EXTRA_HINT} (importing the v0 namespace failed on {exc.name!r}: {exc})",
                    name=f"{__name__}.v0",
                ) from exc
            # The bundle resolves its exports lazily, so a missing v0-sdk extra
            # would only surface on the caller's next line. Read one export now
            # so `from daytona import v0` itself raises the extra hint (the
            # bundle's __getattr__ turns the missing dependency into it).
            try:
                getattr(module, "_Daytona", None)
            except ImportError:
                # Undo the import's bindings, or a retry (a re-run notebook
                # cell) would find them and succeed silently.
                import sys

                sys.modules.pop(f"{__name__}.v0", None)
                globals().pop("v0", None)
                raise
            return module
        in_v0, in_v2 = _moved_to(name) if not name.startswith("_") else (False, False)
        category = _FORMER_ROOT_ALIASES.get(name)
        if category is not None:
            raise ImportError(
                f"`{name}` is not exported from the `daytona` package root since SDK 2.0. To catch it in "
                f"both API versions, use the root `{category}`, the category it named; for the 0.x class "
                f"itself, `from daytona.v0 import {name}` "
                f"({_V0_EXTRA_HINT[0].lower()}{_V0_EXTRA_HINT[1:]}).",
                name=__name__,
            )
        if in_v0 or in_v2:
            where = []
            if in_v0:
                where.append(
                    f"`from daytona.v0 import {name}` for the API existing code was written against "
                    f"({_V0_EXTRA_HINT[0].lower()}{_V0_EXTRA_HINT[1:]})"
                )
            if in_v2:
                where.append(f"`from daytona.v2 import {name}` for the new one")
            raise ImportError(
                f"`{name}` is not exported from the `daytona` package root. Since SDK 2.0 the root holds only "
                "the clients (`Daytona`, `AsyncDaytona`), `DaytonaConfig` and the errors both API versions share, "
                "and every operation goes through a version namespace (`Daytona().v0`, `Daytona().v2`). Import "
                f"it with {' or '.join(where)}.",
                name=__name__,
            )
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
