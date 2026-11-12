# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import warnings
from types import TracebackType
from typing import TYPE_CHECKING, Mapping, Optional

from typing_extensions import Self

from ._core import refuse_dot_segment
from ._transport import AsyncTransport
from .errors import DaytonaError, ProcessNotFoundError, SessionNotFoundError, create_error
from .types import KeepLogs, ProcessInfo, ProcessResult, RunKeepLogs, SessionJSON, StdinMode

if TYPE_CHECKING:
    from .process import AsyncProcessHandle, AsyncProcessService


def _require_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise create_error(f"session identity needs a non-empty string {field}", 400, "BAD_REQUEST", "SDK")
    if field == "sessionId":
        refuse_dot_segment(value, "session id")
    return value


class AsyncSession:
    """One persistent shell whose commands run one at a time, in order.

    Directory changes, exports and functions carry over between commands.
    Set cwd and env when calling ``process.create_session()``. Interrupt a
    running command with ``handle.kill(signal="INT")``; the session survives.
    Sessions do not survive a sandbox stop/start. After ``set -x`` or
    ``set -v``, later commands' stderr also includes the session's command
    execution traces.

    Returned by ``process.create_session()`` / ``process.connect_session()``,
    never constructed directly.
    """

    def __init__(
        self,
        _sandbox_id: str,
        _session_id: str,
        _transport: Optional[AsyncTransport] = None,
    ) -> None:
        from .process import AsyncProcessHandle

        # A missing id must never degrade a session command into a plain,
        # stateless exec: refuse it before any request can be built.
        self.sandbox_id: str = _require_id(_sandbox_id, "sandboxId")
        self.id: str = _require_id(_session_id, "sessionId")
        self._handle = AsyncProcessHandle(_sandbox_id, _session_id, _transport, "session")

    def __repr__(self) -> str:
        return f"AsyncSession(sandbox_id={self.sandbox_id!r}, id={self.id!r})"

    def to_json(self) -> SessionJSON:
        """Return a JSON-safe identity for ``process.connect_session()``."""
        return {"sandboxId": self.sandbox_id, "sessionId": self.id}

    @classmethod
    def _from_json(cls, value: Mapping[str, object]) -> AsyncSession:
        """An unbound session for a serialized identity, without a request."""
        return cls(
            _require_id(value.get("sandboxId"), "sandboxId"), _require_id(value.get("sessionId"), "sessionId")
        )

    async def run(
        self,
        command: str,
        /,
        *,
        stdin: StdinMode = "closed",
        timeout: Optional[float] = None,
        execution_timeout: Optional[float] = None,
        name: Optional[str] = None,
        keep_logs: Optional[RunKeepLogs] = None,
    ) -> ProcessResult:
        """Run a command in this shell and collect output; timeouts are seconds.

        ``timeout`` bounds the caller's wait without stopping the command;
        ``execution_timeout`` stops the command without ending the session
        (omitted or ``0``: no deadline). ``stdin="open"`` is refused: use
        :meth:`start` for a command that reads stdin. A
        command that has not stopped 5 s after its timeout (the shell could
        not interrupt it and it runs no process to kill) ends the session
        instead. In dash, BusyBox ``sh`` and mksh an interrupt leaves only the
        innermost shell function or sourced file, as it does in bash older than
        4.4 (macOS's ``/bin/bash``) when a DEBUG trap is set.
        """
        return await self._process().run(
            command,
            stdin=stdin,
            timeout=timeout,
            execution_timeout=execution_timeout,
            name=name,
            keep_logs=keep_logs,
        )

    async def start(
        self,
        command: str,
        /,
        *,
        stdin: StdinMode = "closed",
        execution_timeout: Optional[float] = None,
        name: Optional[str] = None,
        keep_logs: KeepLogs = "on_exit_ttl",
    ) -> AsyncProcessHandle:
        """Queue a command and return its handle without waiting for exit.

        Interrupt it with ``handle.kill(signal="INT")``; the shell survives.
        ``execution_timeout`` is a server-side deadline in seconds (omitted or
        ``0``: none); the clock starts when the command starts, after any queued
        commands ahead of it. ``keep_logs`` defaults to ``"on_exit_ttl"``, as
        for :meth:`ProcessService.start`.
        """
        return await self._process().start(
            command,
            stdin=stdin,
            execution_timeout=execution_timeout,
            name=name,
            keep_logs=keep_logs,
        )

    async def get(self) -> ProcessInfo:
        """Fetch session metadata, including the shell's live cwd."""
        return await self._handle.get()

    async def delete(self) -> None:
        """End this shell and remove it and all its commands."""
        await self._handle.delete()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        # Already deleted in the block: nothing to clean up. A cleanup
        # failure while the block raised is attached to that error rather
        # than replacing it.
        try:
            await self.delete()
        except (ProcessNotFoundError, SessionNotFoundError):
            return
        except DaytonaError as cleanup:
            if exc is None:
                raise
            note = f"session cleanup also failed (the session may still exist): {cleanup}"
            add_note = getattr(exc, "add_note", None)  # Python 3.11+
            if add_note is not None:
                add_note(note)
            else:
                warnings.warn(note, RuntimeWarning, stacklevel=2)

    def _process(self) -> AsyncProcessService:
        from .process import AsyncProcessService

        return AsyncProcessService(
            self.sandbox_id,
            self._handle._require_transport(),
            _session_id=self.id,
        )
