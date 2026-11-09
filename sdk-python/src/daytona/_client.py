# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The root clients: one credential, one config, a namespace per API version.

``Daytona().v2`` is the v2 client (``daytona.v2``); ``Daytona().v0`` is the v0
client, from the v0 SDK bundled into this package at publish time
(``daytona.v0``, installed with the ``v0-sdk`` extra). Both are built on first
access and cached on the instance; a program that never touches ``.v0`` never
imports v0 code. The root has no resource methods.
"""

from __future__ import annotations

import functools
import importlib
import os
import threading
import warnings
from dataclasses import dataclass, field, fields
from types import ModuleType
from typing import TYPE_CHECKING, Callable, Final, Optional, Union

from typing_extensions import Self

# The v2 client classes the root builds (`daytona.v2` does not export them).
from .v2._sync import Daytona as _DaytonaV2
from .v2._transport import SDK_VERSION as _SDK_VERSION
from .v2.daytona import AsyncDaytona as _AsyncDaytonaV2

# Imported at runtime (not only for type checkers) so typing.get_type_hints()
# resolves DaytonaConfig.events; the root already imports daytona.v2.
from .v2.types import SandboxEventsConfig

if TYPE_CHECKING:
    from types import TracebackType

    # The namespace-client types, for annotations (the TypeScript root exports
    # `DaytonaV2Client` / `DaytonaV0Client` too). At runtime they are
    # annotation-only stand-ins (below): they cannot build a client.
    from .v0 import _AsyncDaytona as AsyncDaytonaV0Client

    # Bundled at publish time; absent from a source checkout (mypy skips it).
    # The bundle hides the client classes behind these private names.
    from .v0 import _AsyncDaytona as _AsyncDaytonaV0
    from .v0 import _Daytona as DaytonaV0Client
    from .v0 import _Daytona as _DaytonaV0
    from .v2._sync import Daytona as DaytonaV2Client
    from .v2.daytona import AsyncDaytona as AsyncDaytonaV2Client
    from .v2.types import DaytonaConfig as _DaytonaConfigV2

# v0's own production API. `d.v0` always passes it explicitly, so the bundled
# v0 client never falls back to `DAYTONA_API_URL` (the 0.x SDK's variable,
# which nothing in SDK 2.x reads).
V0_API_URL: Final[str] = "https://app.daytona.io/api"


def _v0_api_url() -> str:
    """`DAYTONA_V0_API_URL` (dev / non-prod only; blank = unset), else
    V0_API_URL. Read when the v0 client is built and passed explicitly, so the
    0.x SDK's own DAYTONA_API_URL / .env lookup never wins. v0 derives its
    toolbox / proxy URLs from responses of that API, not from the env."""
    return (os.environ.get("DAYTONA_V0_API_URL") or "").strip().rstrip("/") or V0_API_URL


# The same text as the TypeScript SDK's runtime warning (parity).
TARGET_DEPRECATION: Final[str] = (
    "DaytonaConfig.target is deprecated and applies only to the v0 namespace (Daytona().v0). "
    "Pass target per v0 sandbox create (CreateSandboxFromSnapshotParams(target=...)) or "
    "region_id per v0 snapshot create (CreateSnapshotParams(region_id=...)) instead."
)

_V0_EXTRA_HINT: Final[str] = 'The v0 namespace needs the "v0-sdk" extra: pip install "daytona[v0-sdk]"'

# The same text as the TypeScript SDK's (parity).
_CUSTOM_API_URL: Final[str] = "SDK 2.x targets Daytona Cloud. For a custom API URL, stay on SDK 0.x."

# The root form of each client class neither namespace exports.
_CLIENT_ROOT_FORMS: Final[dict[str, str]] = {
    "Daytona": "`from daytona import Daytona`, then `Daytona().{ns}`",
    "AsyncDaytona": "`from daytona import AsyncDaytona`, then `AsyncDaytona().{ns}`",
    "DaytonaConfig": "`from daytona import Daytona, DaytonaConfig`, then `Daytona(DaytonaConfig(...)).{ns}`",
}


class _AnnotationOnly(type):
    """Metaclass of the root's namespace-client types (``DaytonaV2Client``, ...).

    They exist so code can annotate a stored client and ``isinstance``-check
    one, but they must not build a client: the root is the only way, and a
    directly built v0 client would bypass the root's API URL restrictions.
    Calling one raises a ``TypeError``
    naming the root form; instance and subclass checks delegate to the real
    class, loaded on first use (the v0 one needs the bundle).
    """

    _load: Callable[[], type]
    _root_form: str

    def __new__(mcs, name: str, bases: tuple[type, ...], namespace: dict[str, object]) -> _AnnotationOnly:
        guarded = next((base for base in bases if isinstance(base, _AnnotationOnly)), None)
        if guarded is not None:
            raise TypeError(
                f"`daytona.{guarded.__name__}` is a type for annotations and cannot be subclassed: "
                f"the client is built only through the root client. Use {guarded._root_form}."
            )
        return super().__new__(mcs, name, bases, namespace)

    def __call__(cls, *_args: object, **_kwargs: object) -> None:  # type: ignore[override]
        raise TypeError(
            f"`daytona.{cls.__name__}` is a type for annotations, not a constructor: "
            f"the client is built only through the root client. Use {cls._root_form}."
        )

    def __instancecheck__(cls, instance: object) -> bool:
        return isinstance(instance, cls._load())

    def __subclasscheck__(cls, subclass: type) -> bool:
        return subclass is cls or issubclass(subclass, cls._load())


def _annotation_only(name: str, load: Callable[[], type], client: str, namespace: str) -> type:
    root_form = _CLIENT_ROOT_FORMS[client].format(ns=namespace)
    return _AnnotationOnly(
        name, (), {"_load": staticmethod(load), "_root_form": root_form, "__module__": "daytona"}
    )


def _load_v0_class(private_name: str) -> type:
    loaded: object = _load_v0(private_name)[1]
    assert isinstance(loaded, type)
    return loaded


if not TYPE_CHECKING:
    DaytonaV2Client = _annotation_only("DaytonaV2Client", lambda: _DaytonaV2, "Daytona", "v2")
    AsyncDaytonaV2Client = _annotation_only(
        "AsyncDaytonaV2Client", lambda: _AsyncDaytonaV2, "AsyncDaytona", "v2"
    )
    DaytonaV0Client = _annotation_only("DaytonaV0Client", lambda: _load_v0_class("_Daytona"), "Daytona", "v0")
    AsyncDaytonaV0Client = _annotation_only(
        "AsyncDaytonaV0Client", lambda: _load_v0_class("_AsyncDaytona"), "AsyncDaytona", "v0"
    )


def _client_not_exported(namespace: str, name: str) -> str:
    """The ImportError text for ``from daytona.v0 import Daytona`` /
    ``from daytona.v2 import Daytona`` (and the other hidden client classes and
    their suffixed aliases); raised by each namespace's ``__getattr__``."""
    base = name[:-2] if name not in _CLIENT_ROOT_FORMS and name[-2:] in ("V0", "V2") else name
    message = (
        f"`daytona.{namespace}.{name}` is not exported: the {namespace} client is built only through "
        f"the root client. Use {_CLIENT_ROOT_FORMS[base].format(ns=namespace)}."
    )
    return f"{message} {_CUSTOM_API_URL}" if namespace == "v0" else message


@dataclass
class DaytonaConfig:
    """Configuration shared by every API version.

    One credential serves both namespaces: ``api_key`` (``DAYTONA_API_KEY``)
    or ``jwt_token`` (``DAYTONA_JWT_TOKEN``). A JWT is scoped by
    ``organization_id`` for v0 and ``project_id`` for v2. Fields left unset
    are read from the environment by each namespace.

    There is no region or API URL field here. Pass ``region=`` to a v2 create.
    ``DAYTONA_V2_API_URL`` (a regional endpoint or a test environment) selects the v2 API
    endpoint and never reaches v0; ``DAYTONA_API_URL`` belongs to the 0.x SDK
    and SDK 2.x never reads it.

    ``Daytona(config)`` / ``AsyncDaytona(config)`` copy the fields when the
    client is built: changing the object afterwards has no effect on it.

    ``target`` is deprecated and applies to ``.v0`` only (the v0 client's
    default region). Pass ``target`` to each v0 sandbox create
    (``CreateSandboxFromSnapshotParams(target=...)``) or ``region_id`` to each
    v0 snapshot create instead. Setting it still works and emits a
    ``DeprecationWarning``.
    ``request_timeout`` (seconds; default 60, ``0`` = no limit) is the total
    time one ordinary v2 request may take; waits and streams are not cut by
    it, and file transfers are bounded by it only while idle (no bytes moving
    for that long; with ``0``, not at all). ``max_response_bytes`` (an integer up to 2**53 - 1; ``None`` keeps the 1 GiB default, ``0`` means no limit, and any other value (negative, fractional, boolean, string, too large) is a ``BadRequestError``)
    caps every v2 response body the SDK holds in memory whole; the in-memory
    file reads take it per call too. ``request_timeout``, ``connection_pool_maxsize`` and
    ``otel_enabled`` tune the v2 client's HTTP transport;
    ``connection_pool_maxsize`` and ``otel_enabled`` reach v0 too.
    ``shared_hub`` and ``events`` (``daytona.v2.SandboxEventsConfig``) configure
    the v2 client's sandbox-state events hub; v0 has no such hub.
    """

    api_key: Optional[str] = field(default=None, repr=False)
    jwt_token: Optional[str] = field(default=None, kw_only=True, repr=False)
    organization_id: Optional[str] = field(default=None, kw_only=True)
    project_id: Optional[str] = field(default=None, kw_only=True)
    target: Optional[str] = field(default=None, kw_only=True)
    request_timeout: Optional[float] = field(default=None, kw_only=True)
    max_response_bytes: Optional[int] = field(default=None, kw_only=True)
    connection_pool_maxsize: Optional[int] = field(default=None, kw_only=True)
    otel_enabled: Optional[bool] = field(default=None, kw_only=True)
    shared_hub: bool = field(default=True, kw_only=True)
    events: Optional[SandboxEventsConfig] = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.target is not None:
            # __post_init__ <- dataclass __init__ <- _config_init (below) <- the caller.
            warnings.warn(TARGET_DEPRECATION, DeprecationWarning, stacklevel=4)


_REMOVED_URL_FIELD: Final[str] = "DaytonaConfig does not take {name!r}: " + _CUSTOM_API_URL
_dataclass_init = DaytonaConfig.__init__


@functools.wraps(_dataclass_init)
def _config_init(self: DaytonaConfig, *args: object, **kwargs: object) -> None:
    # A 0.x config passing api_url / toolbox_url would otherwise get Python's
    # generic "unexpected keyword argument"; name the replacement instead (the
    # TS root does the same).
    for name in ("api_url", "server_url", "toolbox_url"):
        if name in kwargs:
            raise TypeError(_REMOVED_URL_FIELD.format(name=name))
    _dataclass_init(self, *args, **kwargs)  # type: ignore[arg-type]


DaytonaConfig.__init__ = _config_init  # type: ignore[method-assign]

# 0.x configs carried their API URL; the root has none (SDK 2.x targets Daytona
# Cloud). A field set after construction (`cfg.api_url = ...`, a __dict__ write,
# a hook) would otherwise be ignored, and the root would send the config's key
# to Daytona Cloud, so it is refused when set and again when a client is built.
_REMOVED_URL_FIELDS: Final = ("api_url", "server_url", "toolbox_url")


def _config_setattr(self: DaytonaConfig, name: str, value: object) -> None:
    if name in _REMOVED_URL_FIELDS:
        raise TypeError(_REMOVED_URL_FIELD.format(name=name))
    object.__setattr__(self, name, value)


DaytonaConfig.__setattr__ = _config_setattr  # type: ignore[method-assign,assignment]


def _refuse_url_fields(config: DaytonaConfig) -> None:
    for name in _REMOVED_URL_FIELDS:
        if name in vars(config) or hasattr(config, name):
            raise TypeError(_REMOVED_URL_FIELD.format(name=name))


def _snapshot_config(config: DaytonaConfig) -> DaytonaConfig:
    """A private copy of ``config``'s fields, taken when the client is built.

    Later changes to the caller's object do not reach the client (the
    TypeScript root copies its config the same way). URL fields are refused
    before and after the copy: a property may add one while the fields are
    read.
    """
    _refuse_url_fields(config)
    snapshot = object.__new__(DaytonaConfig)
    for config_field in fields(DaytonaConfig):
        object.__setattr__(snapshot, config_field.name, getattr(config, config_field.name))
    _refuse_url_fields(config)
    return snapshot


def _load_v0(client_name: str) -> tuple[ModuleType, object]:
    """Import the bundled v0 namespace, or explain how to install it.

    Missing in two cases, one message: the ``v0-sdk`` extra is not installed (the
    package is there, an import inside it fails), or this build carries no
    bundle at all (a source checkout).

    The bundled package resolves its exports lazily (a module ``__getattr__``),
    so importing it succeeds even without the extra; the missing dependency
    surfaces only when the client classes are first read (under the private
    names the bundle gives them). Both are read here, inside the same ``try``,
    so that failure gets the hint too. Returns the module and the requested
    client class.
    """
    try:
        module = importlib.import_module("daytona.v0")
        client_class: object = getattr(module, client_name)
        _config_class: object = module._DaytonaConfig  # resolved here so a missing dependency gets the hint
        return module, client_class
    except ModuleNotFoundError as error:
        # A missing module is what the extra fixes: the bundle itself (a
        # source checkout) or one of the dependencies the extra installs.
        missing = error.name
        if missing is None or missing == "daytona.v0":
            raise ImportError(_V0_EXTRA_HINT, name="daytona.v0") from error
        raise ImportError(
            f"{_V0_EXTRA_HINT} (importing the v0 namespace failed on {missing!r}: {error})",
            name="daytona.v0",
        ) from error
    except ImportError as error:
        if str(error).startswith(_V0_EXTRA_HINT):
            # The bundle already named the extra (its lazy __getattr__ turns a
            # missing dependency into this hint): pass it through unchanged.
            raise
        # Anything else (a broken bundle, a dependency that raises ImportError
        # on import) is not fixed by installing the extra: say what failed.
        raise ImportError(f"importing the v0 namespace failed: {error}", name="daytona.v0") from error


def _v2_config(config: DaytonaConfig) -> _DaytonaConfigV2:
    _refuse_url_fields(config)
    from .v2.types import DaytonaConfig as DaytonaConfigV2

    v2_config = DaytonaConfigV2(
        api_key=config.api_key,
        jwt_token=config.jwt_token,
        project_id=config.project_id,
        request_timeout=config.request_timeout,
        max_response_bytes=config.max_response_bytes,
        connection_pool_maxsize=config.connection_pool_maxsize,
        otel_enabled=config.otel_enabled,
        shared_hub=config.shared_hub,
        events=config.events,
    )
    # A property may add a URL while the config fields are copied.
    _refuse_url_fields(config)
    return v2_config


# Where each root-config field goes; a test checks this covers every field and
# matches what _v0_config / _v2_config forward. Mirrors the TypeScript SDK's
# ROOT_CONFIG_ROUTING.
ROOT_CONFIG_ROUTING: Final[dict[str, str]] = {
    "api_key": "both",
    "jwt_token": "both",
    "organization_id": "v0",
    "target": "v0",
    "project_id": "v2",
    "request_timeout": "v2",
    "max_response_bytes": "v2",
    "connection_pool_maxsize": "both",
    "otel_enabled": "both",
    "shared_hub": "v2",
    "events": "v2",
}


def _v0_config(module: ModuleType, config: DaytonaConfig) -> object:
    _refuse_url_fields(config)
    tuning: dict[str, Union[int, bool]] = {}
    # v0 reads None as "unlimited" / "use the env var"; pass only what was set.
    if config.connection_pool_maxsize is not None:
        tuning["connection_pool_maxsize"] = config.connection_pool_maxsize
    if config.otel_enabled is not None:
        tuning["otel_enabled"] = config.otel_enabled
    v0_config: object = module._DaytonaConfig(
        api_key=config.api_key,
        jwt_token=config.jwt_token,
        organization_id=config.organization_id,
        api_url=_v0_api_url(),
        target=config.target,
        **tuning,
    )
    _refuse_url_fields(config)
    return v0_config


def _close_sync_v0(v0: object) -> None:
    """Release a sync v0 client's resources. The bundled 0.x sync ``Daytona``
    has no ``close()`` but opens a socket.io connection on a background thread
    (and, with ``otel_enabled``, a tracer provider), so release those the way
    its async twin's ``close()`` does."""
    close = getattr(v0, "close", None)
    if callable(close):
        close()
        return
    # Release every resource even if one step raises; re-raise the first error.
    first: Optional[BaseException] = None
    for owner, method in (
        ("_tracer_provider", "shutdown"),
        ("_subscription_manager", "shutdown"),
        ("_event_dispatcher", "disconnect"),
    ):
        release = getattr(getattr(v0, owner, None), method, None)
        if callable(release):
            try:
                release()
            except BaseException as error:
                first = _prefer_interrupt(first, error)
    if first is not None:
        raise first


def _prefer_interrupt(first: Optional[BaseException], error: BaseException) -> BaseException:
    """Which close() failure to re-raise: the first one, unless a later one is a
    cancellation or interrupt (not an ``Exception``), which must propagate so
    the caller's cancellation is not converted into an ordinary error."""
    if first is None:
        return error
    if isinstance(first, Exception) and not isinstance(error, Exception):
        error.__context__ = first
        return error
    return first


class _Root:
    """State shared by the sync and async root clients."""

    def __init__(self, config: Optional[DaytonaConfig] = None) -> None:
        # Snapshot now: later edits to the caller's config do not reach the
        # namespace clients, which are built from it on first access.
        self._config = _snapshot_config(config) if config is not None else DaytonaConfig()
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    # Defined only at runtime, so type checkers keep reporting `d.create` on the
    # root (the same reason daytona/__init__.py hides its module __getattr__).
    if not TYPE_CHECKING:

        def __getattribute__(self, name: str) -> object:
            """Guide 0.x code that reads a client member on the root.

            Wraps normal lookup instead of defining ``__getattr__``: Python
            falls back to ``__getattr__`` when a property getter (``v0`` /
            ``v2``) raises AttributeError, which would hide that error or
            re-run the getter. Here every error from a real attribute
            propagates unchanged; only a name the root does not define and
            the 0.x client had gets guidance.
            """
            try:
                return object.__getattribute__(self, name)
            except AttributeError:
                if name in _MOVED_TO_V0 and not any(name in vars(cls) for cls in type(self).__mro__):
                    client = type(self).__name__
                    raise AttributeError(_moved_to_v0_message(client, name), name=name, obj=self) from None
                raise

        def __setattr__(self, name: str, value: object) -> None:
            """Refuse 0.x client settings on the root (``d.default_language = ...``):
            they configure the v0 client, so the assignment would silently do nothing.
            Subclass instances may assign any name, as before SDK 2.x."""
            if name in _MOVED_TO_V0 and type(self).__module__ == __name__:
                raise AttributeError(_moved_to_v0_message(type(self).__name__, name), name=name, obj=self)
            object.__setattr__(self, name, value)


# The 0.x client's members, which SDK 2.x moved to `.v0` (the 0.x Daytona /
# AsyncDaytona at the pinned tag; upgrade/mapping/mapping.json
# `movedClientMembers.py` is the same list, and test_root_client.py pins both).
_MOVED_TO_V0: Final = frozenset(
    (
        "create",
        "get",
        "list",
        "start",
        "stop",
        "delete",
        "volume",
        "snapshot",
        "secret",
        "warm_pool",
        "default_language",
    )
)


def _moved_to_v0_message(client: str, name: str) -> str:
    # The migration tool is published with every SDK release under the same
    # version, so the guidance names the release that matches this SDK (a
    # source build names its placeholder version).
    tool = "daytona-upgrade" if _SDK_VERSION == "unknown" else f"daytona-upgrade@{_SDK_VERSION}"
    return (
        f"SDK 2.x moved {client}.{name} to {client}.v0.{name} (the Daytona v0 platform; "
        f"use {client}.v2 for Daytona v2). To migrate 0.x code, run uvx {tool}."
    )


class Daytona(_Root):
    """The synchronous client: ``Daytona().v0`` and ``Daytona().v2``.

    Every operation goes through a version namespace; the root itself only
    holds the shared configuration. ``with Daytona() as d:`` (or ``close()``)
    closes whichever namespace clients were built.
    """

    def __init__(self, config: Optional[DaytonaConfig] = None) -> None:
        super().__init__(config)
        self._v2: Optional[_DaytonaV2] = None
        self._v0: Optional[_DaytonaV0] = None

    @property
    def v2(self) -> _DaytonaV2:
        """The v2 client (v2's synchronous client), built on first access.

        Owned by the root: close the root, not this client (a closed v2 client
        stays cached and its calls fail).
        """
        with self._lock:
            if self._v2 is None:
                self._v2 = _DaytonaV2(_v2_config(self._config))
            return self._v2

    @property
    def v0(self) -> _DaytonaV0:
        """The v0 client (the bundled 0.x ``Daytona``), built on first access.

        Needs the ``v0-sdk`` extra (``pip install "daytona[v0-sdk]"``); raises
        ``ImportError`` naming it otherwise.
        """
        with self._lock:
            if self._v0 is None:
                module, client_class = _load_v0("_Daytona")
                self._v0 = client_class(_v0_config(module, self._config))  # type: ignore[operator]
            return self._v0

    def close(self) -> None:
        """Close the namespace clients built so far. Idempotent.

        Closes the v2 client and the v0 client. The bundled 0.x sync
        ``Daytona`` has no ``close()``, so the root releases its event socket
        (and tracer provider) directly.
        """
        with self._lock:
            v2, self._v2 = self._v2, None
            v0, self._v0 = self._v0, None
        # Close both even if the first raises; re-raise the first error after.
        first: Optional[BaseException] = None
        if v2 is not None:
            try:
                v2.close()
            except BaseException as error:
                first = error
        if v0 is not None:
            try:
                _close_sync_v0(v0)
            except BaseException as error:
                first = _prefer_interrupt(first, error)
        if first is not None:
            raise first

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()


class AsyncDaytona(_Root):
    """The asynchronous client: ``AsyncDaytona().v0`` and ``AsyncDaytona().v2``.

    Same shape as :class:`Daytona`; the namespace clients are the async ones.
    ``async with AsyncDaytona() as d:`` (or ``await close()``) closes whichever
    namespace clients were built.
    """

    def __init__(self, config: Optional[DaytonaConfig] = None) -> None:
        super().__init__(config)
        self._v2: Optional[_AsyncDaytonaV2] = None
        self._v0: Optional[_AsyncDaytonaV0] = None

    @property
    def v2(self) -> _AsyncDaytonaV2:
        """The v2 client (v2's asynchronous client), built on first access.

        Owned by the root: close the root, not this client (a closed v2 client
        stays cached and its calls fail).
        """
        with self._lock:
            if self._v2 is None:
                self._v2 = _AsyncDaytonaV2(_v2_config(self._config))
            return self._v2

    @property
    def v0(self) -> _AsyncDaytonaV0:
        """The v0 client (the bundled 0.x ``AsyncDaytona``), built on first access.

        Needs the ``v0-sdk`` extra (``pip install "daytona[v0-sdk]"``); raises
        ``ImportError`` naming it otherwise.
        """
        with self._lock:
            if self._v0 is None:
                module, client_class = _load_v0("_AsyncDaytona")
                self._v0 = client_class(_v0_config(module, self._config))  # type: ignore[operator]
            return self._v0

    async def close(self) -> None:
        """Close the namespace clients built so far. Idempotent."""
        with self._lock:
            v2, self._v2 = self._v2, None
            v0, self._v0 = self._v0, None
        # Close both even if the first raises; re-raise the first error after.
        first: Optional[BaseException] = None
        if v2 is not None:
            try:
                await v2.close()
            except BaseException as error:
                first = error
        close_v0 = getattr(v0, "close", None)
        if callable(close_v0):
            try:
                await close_v0()
            except BaseException as error:
                first = _prefer_interrupt(first, error)
        if first is not None:
            raise first

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.close()
