# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""A header the HTTP client refuses to send never reaches error text.

The HTTP stack quotes the refused header (``Illegal header value b'Bearer
<credential>'``) in its own error and in the lower-layer errors it chains, so
the SDK's ``ConnectionError``, its causes, a printed traceback and a telemetry
span must all show it redacted.
"""

from __future__ import annotations

import contextlib
import socket
import traceback
from typing import Iterator, Optional

import httpx
import pytest

from daytona.v2 import ConnectionError
from daytona.v2._auth import ProjectTokenAuth
from daytona.v2._core import RequestSpec
from daytona.v2._otel import _ActiveRequestSpan
from daytona.v2._transport import AsyncTransport, SyncTransport
from daytona.v2._transport_errors import redact_header_text

_SECRET = "dtn_redaction_secret"
_BAD_TOKEN = f"{_SECRET}\n"


@pytest.fixture
def listener() -> Iterator[str]:
    """A TCP listener that never answers: the client connects, then refuses
    to write the request because of its header."""
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(16)
        yield f"http://127.0.0.1:{server.getsockname()[1]}"


def _chain(error: Optional[BaseException]) -> list[BaseException]:
    seen: list[BaseException] = []
    pending = [error]
    while pending:
        current = pending.pop()
        if current is None or any(current is known for known in seen):
            continue
        seen.append(current)
        pending.extend((current.__cause__, current.__context__))
        pending.extend(arg for arg in current.args if isinstance(arg, BaseException))
    return seen


def _assert_redacted(error: BaseException) -> None:
    assert isinstance(error, ConnectionError)
    assert str(error) == "Illegal header value (redacted)"
    printed = "".join(traceback.format_exception(error))
    assert _SECRET not in printed
    assert "Illegal header value (redacted)" in printed
    for link in _chain(error):
        assert _SECRET not in str(link)
        assert _SECRET not in repr(link)


def test_sync_transport_redacts_a_refused_header(listener: str) -> None:
    transport = SyncTransport(listener, _BAD_TOKEN, request_timeout=5)
    try:
        with pytest.raises(ConnectionError) as excinfo:
            transport.send(RequestSpec("GET", "/sandboxes"))
        _assert_redacted(excinfo.value)
        with pytest.raises(ConnectionError) as excinfo:
            transport.open_stream(RequestSpec("GET", "/sandboxes"))
        _assert_redacted(excinfo.value)
    finally:
        transport.close()


@pytest.mark.anyio
async def test_async_transport_redacts_a_refused_header(listener: str) -> None:
    transport = AsyncTransport(listener, _BAD_TOKEN, request_timeout=5)
    try:
        with pytest.raises(ConnectionError) as excinfo:
            await transport.send(RequestSpec("GET", "/sandboxes"))
        _assert_redacted(excinfo.value)
        with pytest.raises(ConnectionError) as excinfo:
            await transport.open_stream(RequestSpec("GET", "/sandboxes"))
        _assert_redacted(excinfo.value)
    finally:
        await transport.close()


@pytest.mark.anyio
async def test_token_exchange_redacts_a_refused_header(listener: str) -> None:
    async with httpx.AsyncClient() as client:
        auth = ProjectTokenAuth(
            api_url=listener,
            jwt_token=_BAD_TOKEN,
            project_id="project",
            source="sdk-python-async",
            client=client,
            request_timeout=5,
        )
        with pytest.raises(ConnectionError) as excinfo:
            await auth.get_token()
    _assert_redacted(excinfo.value)


def test_a_telemetry_span_records_the_redacted_error() -> None:
    recorded: list[str] = []

    class Span:
        def set_attribute(self, key: str, value: object) -> None:
            pass

        def set_status(self, status: object) -> None:
            pass

        def record_exception(self, exception: BaseException) -> None:
            recorded.extend(str(link) for link in _chain(exception))

    lower = ValueError(f"Illegal header value b'Bearer {_SECRET}\\n'")
    error = httpx.LocalProtocolError(str(lower))
    error.__cause__ = lower
    span = _ActiveRequestSpan(
        context_manager=contextlib.nullcontext(Span()),  # type: ignore[arg-type]
        span=Span(),
        status_factory=lambda status: status,
        ok_status="ok",
        error_status="error",
    )
    span.finish(error=error)
    assert recorded == ["Illegal header value (redacted)", "Illegal header value (redacted)"]


def test_redaction_keeps_other_error_text() -> None:
    error = httpx.ConnectError("All connection attempts failed")
    redact_header_text(error)
    assert str(error) == "All connection attempts failed"
    name = httpx.LocalProtocolError(f"Illegal header name b'X-{_SECRET}:'")
    redact_header_text(name)
    assert str(name) == "Illegal header name (redacted)"
