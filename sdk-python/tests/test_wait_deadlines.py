# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""A stalled long-poll read never outlives the wait deadline.

The TypeScript twin is ``tests/sandbox-waits.test.ts`` ("bounds a stalled HTTP
long poll by a fifty-millisecond wait deadline"): a read the deadline cuts is
the wait running out, ``RequestTimeoutError`` (408, ``REQUEST_TIMEOUT``,
``SDK``) for the lifecycle methods, the returned handle for ``create*()``.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from daytona import AsyncDaytona, Daytona, DaytonaConfig
from daytona.v2 import ClientTimeoutError, RequestTimeoutError, SandboxCreateRequestTimeoutError

_DETAIL = {
    "id": "sbx-1",
    "class": "linux",
    "status": "pending",
    "name": "sbx-1",
    "region_id": "region-a",
    "port_map": [],
    "cpu": 1,
    "cores_max": 1,
    "memory_mib": 1024,
    "disk_mib": 2048,
}


class _StallingApi:
    """Answers lifecycle requests at once and holds every `wait=` read for 30 s."""

    def __init__(self, post_hold: float = 0) -> None:
        stop = threading.Event()
        self.stop = stop

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: object) -> None:
                pass

            def _reply(self, status: int, body: object) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                # The client may already have given up on a held request.
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    self.wfile.write(data)

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                if self.command == "GET" and "wait=" in self.path:
                    stop.wait(30)
                    return
                if self.command == "POST" and self.path == "/sandboxes":
                    # The server's create hold, before it answers.
                    stop.wait(post_hold)
                    self._reply(
                        200, {"id": "sbx-1", "status": "pending", "wait_cap_seconds": 60 if post_hold else 0}
                    )
                    return
                self._reply(200, _DETAIL if self.command == "GET" else {})

            do_GET = _handle
            do_POST = _handle
            do_DELETE = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Iterator[_StallingApi]:
    server = _StallingApi(post_hold=getattr(request, "param", 0))
    monkeypatch.setenv("DAYTONA_V2_API_URL", server.url)
    monkeypatch.setenv("DAYTONA_SANDBOX_EVENTS", "0")
    try:
        yield server
    finally:
        server.close()


# A request timeout far above the wait budget: only the wait deadline may end the read.
_CONFIG = DaytonaConfig(api_key="key", request_timeout=30)
# Generous on a busy runner, far below the 5 s read slack the deadline must cut.
_SLACK = 3.0


@pytest.mark.anyio
@pytest.mark.parametrize("verb", ["stop", "delete", "wait"])
async def test_a_stalled_long_poll_ends_at_the_wait_deadline_async(api: _StallingApi, verb: str) -> None:
    async with AsyncDaytona(_CONFIG) as root:
        started = time.monotonic()
        with pytest.raises(RequestTimeoutError) as raised:
            if verb == "wait":
                await (await root.v2.get("sbx-1")).wait(timeout=0.5)
            else:
                await getattr(root.v2, verb)("sbx-1", timeout=0.5)
        elapsed = time.monotonic() - started
    assert not isinstance(raised.value, ClientTimeoutError)
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (
        408,
        "REQUEST_TIMEOUT",
        "SDK",
    )
    assert 0.4 <= elapsed < 0.5 + _SLACK


@pytest.mark.anyio
async def test_a_stalled_long_poll_returns_the_created_handle_at_the_wait_deadline(api: _StallingApi) -> None:
    async with AsyncDaytona(_CONFIG) as root:
        started = time.monotonic()
        sandbox = await root.v2.create("img_1", wait_seconds=1)
        elapsed = time.monotonic() - started
    assert (sandbox.id, sandbox.state) == ("sbx-1", "creating")
    assert 0.9 <= elapsed < 1 + _SLACK


@pytest.mark.parametrize("verb", ["stop", "delete"])
def test_a_stalled_long_poll_ends_at_the_wait_deadline_sync(api: _StallingApi, verb: str) -> None:
    with Daytona(_CONFIG) as root:
        started = time.monotonic()
        with pytest.raises(RequestTimeoutError) as raised:
            getattr(root.v2, verb)("sbx-1", timeout=0.5)
        elapsed = time.monotonic() - started
    assert not isinstance(raised.value, ClientTimeoutError)
    assert 0.4 <= elapsed < 0.5 + _SLACK


# A create POST that asks the server to hold is a long poll: a request timeout
# shorter than the hold does not cut it, and the create returns its handle.
_SHORT = DaytonaConfig(api_key="key", request_timeout=1)


@pytest.mark.anyio
@pytest.mark.parametrize("api", [2.5], indirect=True)
async def test_a_create_hold_longer_than_the_request_timeout_returns_the_handle(api: _StallingApi) -> None:
    async with AsyncDaytona(_SHORT) as root:
        sandbox = await root.v2.create("img_1", wait_seconds=3)
    assert (sandbox.id, sandbox.state) == ("sbx-1", "creating")


@pytest.mark.parametrize("api", [2.5], indirect=True)
def test_a_create_hold_longer_than_the_request_timeout_returns_the_handle_sync(api: _StallingApi) -> None:
    with Daytona(_SHORT) as root:
        sandbox = root.v2.create("img_1", wait_seconds=3)
    assert sandbox.id == "sbx-1"


@pytest.mark.anyio
@pytest.mark.parametrize("api", [2.5], indirect=True)
async def test_a_create_without_a_hold_stays_an_ordinary_request(api: _StallingApi) -> None:
    # wait_seconds=0 asks for no hold: the request timeout bounds the POST.
    async with AsyncDaytona(_SHORT) as root:
        with pytest.raises(SandboxCreateRequestTimeoutError):
            await root.v2.create("img_1", wait_seconds=0)
