# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# pylint: disable=protected-access

# The SDK 1.x async root client. Imports only the async v1 client chain (no sync modules).

from __future__ import annotations

from collections.abc import AsyncIterator
from types import TracebackType
from typing import Callable, overload

from typing_extensions import deprecated

from ._async.daytona import AsyncDaytona as _AsyncDaytonaV1
from ._async.sandbox import AsyncSandbox
from ._async.secret import AsyncSecretService
from ._async.snapshot import AsyncSnapshotService
from ._async.volume import AsyncVolumeService
from ._async.warm_pool import AsyncWarmPoolService
from ._versioning import warn_root_namespace_once
from .common.daytona import CodeLanguage, CreateSandboxFromImageParams, CreateSandboxFromSnapshotParams, DaytonaConfig
from .common.sandbox import ListSandboxesQuery


class AsyncDaytona:
    """The SDK 1.x async root client.

    It owns one async v1 client, returned by `v1` (the same instance on every access); use
    `daytona.v1.<member>`, the form SDK 2.x keeps. `close()` and the async context manager are not
    deprecated. Every other member forwards to `v1` and is deprecated: the first use in a process
    emits one `DeprecationWarning`.

    Example:
        ```python
        from daytona import AsyncDaytona

        async with AsyncDaytona() as daytona:
            sandbox = await daytona.v1.create()
        ```
    """

    _v1: _AsyncDaytonaV1

    def __init__(self, config: DaytonaConfig | None = None):
        """Create the root client and its async v1 client from `config` (or the environment)."""
        self._v1 = _AsyncDaytonaV1(config)

    @property
    def v1(self) -> _AsyncDaytonaV1:
        """The async v1 client (`daytona.v1.AsyncDaytona`); the same instance on every access."""
        return self._v1

    async def __aenter__(self) -> "AsyncDaytona":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the async v1 client. Not deprecated."""
        await self.v1.close()

    @property
    @deprecated("Use `daytona.v1.default_language` instead. The root form is removed in SDK 2.x.", category=None)
    def default_language(self) -> CodeLanguage:
        warn_root_namespace_once("default_language")
        return self.v1.default_language

    @default_language.setter
    @deprecated("Use `daytona.v1.default_language` instead. The root form is removed in SDK 2.x.", category=None)
    def default_language(self, value: CodeLanguage) -> None:
        warn_root_namespace_once("default_language")
        self.v1.default_language = value

    @property
    @deprecated("Use `daytona.v1.volume` instead. The root form is removed in SDK 2.x.", category=None)
    def volume(self) -> AsyncVolumeService:
        warn_root_namespace_once("volume")
        return self.v1.volume

    @volume.setter
    @deprecated("Use `daytona.v1.volume` instead. The root form is removed in SDK 2.x.", category=None)
    def volume(self, value: AsyncVolumeService) -> None:
        warn_root_namespace_once("volume")
        self.v1.volume = value

    @property
    @deprecated("Use `daytona.v1.snapshot` instead. The root form is removed in SDK 2.x.", category=None)
    def snapshot(self) -> AsyncSnapshotService:
        warn_root_namespace_once("snapshot")
        return self.v1.snapshot

    @snapshot.setter
    @deprecated("Use `daytona.v1.snapshot` instead. The root form is removed in SDK 2.x.", category=None)
    def snapshot(self, value: AsyncSnapshotService) -> None:
        warn_root_namespace_once("snapshot")
        self.v1.snapshot = value

    @property
    @deprecated("Use `daytona.v1.secret` instead. The root form is removed in SDK 2.x.", category=None)
    def secret(self) -> AsyncSecretService:
        warn_root_namespace_once("secret")
        return self.v1.secret

    @secret.setter
    @deprecated("Use `daytona.v1.secret` instead. The root form is removed in SDK 2.x.", category=None)
    def secret(self, value: AsyncSecretService) -> None:
        warn_root_namespace_once("secret")
        self.v1.secret = value

    @property
    @deprecated("Use `daytona.v1.warm_pool` instead. The root form is removed in SDK 2.x.", category=None)
    def warm_pool(self) -> AsyncWarmPoolService:
        warn_root_namespace_once("warm_pool")
        return self.v1.warm_pool

    @warm_pool.setter
    @deprecated("Use `daytona.v1.warm_pool` instead. The root form is removed in SDK 2.x.", category=None)
    def warm_pool(self, value: AsyncWarmPoolService) -> None:
        warn_root_namespace_once("warm_pool")
        self.v1.warm_pool = value

    @overload
    @deprecated("Use `daytona.v1.create` instead. The root form is removed in SDK 2.x.", category=None)
    async def create(
        self,
        params: CreateSandboxFromSnapshotParams | None = None,
        *,
        timeout: float = 60,
    ) -> AsyncSandbox: ...

    @overload
    @deprecated("Use `daytona.v1.create` instead. The root form is removed in SDK 2.x.", category=None)
    async def create(
        self,
        params: CreateSandboxFromImageParams | None = None,
        *,
        timeout: float = 60,
        on_snapshot_create_logs: Callable[[str], None] | None = None,
    ) -> AsyncSandbox: ...

    @deprecated("Use `daytona.v1.create` instead. The root form is removed in SDK 2.x.", category=None)
    async def create(
        self,
        params: CreateSandboxFromSnapshotParams | CreateSandboxFromImageParams | None = None,
        *,
        timeout: float = 60,
        on_snapshot_create_logs: Callable[[str], None] | None = None,
    ) -> AsyncSandbox:
        warn_root_namespace_once("create")
        # Forward exactly what the implementation accepts (snapshot or image params, and the log callback
        # with either); its public overloads cannot express that union, so call it untyped.
        create = getattr(self.v1, "create")
        return await create(params, timeout=timeout, on_snapshot_create_logs=on_snapshot_create_logs)

    @deprecated("Use `daytona.v1.delete` instead. The root form is removed in SDK 2.x.", category=None)
    async def delete(self, sandbox: AsyncSandbox, timeout: float = 60, wait: bool = False) -> None:
        warn_root_namespace_once("delete")
        await self.v1.delete(sandbox, timeout, wait=wait)

    @deprecated("Use `daytona.v1.get` instead. The root form is removed in SDK 2.x.", category=None)
    async def get(self, sandbox_id_or_name: str, request_timeout: float | None = None) -> AsyncSandbox:
        warn_root_namespace_once("get")
        return await self.v1.get(sandbox_id_or_name, request_timeout=request_timeout)

    @deprecated("Use `daytona.v1.list` instead. The root form is removed in SDK 2.x.", category=None)
    def list(
        self,
        query: ListSandboxesQuery | None = None,
        request_timeout: float | None = None,
    ) -> AsyncIterator[AsyncSandbox]:
        warn_root_namespace_once("list")
        return self.v1.list(query, request_timeout=request_timeout)

    @deprecated("Use `daytona.v1.start` instead. The root form is removed in SDK 2.x.", category=None)
    async def start(self, sandbox: AsyncSandbox, timeout: float = 60) -> None:
        warn_root_namespace_once("start")
        await self.v1.start(sandbox, timeout)

    @deprecated("Use `daytona.v1.stop` instead. The root form is removed in SDK 2.x.", category=None)
    async def stop(self, sandbox: AsyncSandbox, timeout: float = 60) -> None:
        warn_root_namespace_once("stop")
        await self.v1.stop(sandbox, timeout)
