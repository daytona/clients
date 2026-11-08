# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Real HTTP deadline coverage for async pulls and their generated sync twin."""

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import anyio
import pytest

from daytona.v2._bridge import SyncBridge
from daytona.v2._sync.services import ImagesService
from daytona.v2._transport import AsyncTransport
from daytona.v2.errors import ClientTimeoutError, RequestTimeoutError
from daytona.v2.services import AsyncImagesService
from daytona.v2.types import Image


@dataclass(frozen=True, slots=True)
class Peer:
    url: str
    requests: list[str]
    disconnected: Event


Pull = Callable[[Peer, float | None, float | None], Awaitable[Image]]


@contextmanager
def pull_peer(phase: int, delay: float | None, *, ready: bool = False) -> Iterator[Peer]:
    requests: list[str] = []
    disconnected = Event()
    release = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.respond()

        def do_GET(self) -> None:
            self.respond()

        def respond(self) -> None:
            requests.append(self.path)
            target = len(requests) == phase
            if target and delay is None:
                # EOF is observable proof that expiry cancelled the HTTP request.
                if self.rfile.read(1) == b"":
                    disconnected.set()
                return
            if target:
                release.wait(delay)
            payload = (
                b'{"message":"fixture request timeout","code":"REQUEST_TIMEOUT","source":"API"}'
                if target and not ready
                else b'{"id":"img-pull","class":"linux","updated_at":"2026-01-01T00:00:00Z","retention":"permanent","region_ids":[],"anonymous":false,"format_version":1,"format_outdated":false,"status":"ready","region_id":"region-c","origin":"registry","created_at":"2026-01-01T00:00:00Z"}'
                if target and ready
                else b'{"id":"img-pull","class":"linux","updated_at":"2026-01-01T00:00:00Z","retention":"permanent","region_ids":[],"anonymous":false,"format_version":1,"format_outdated":false,"status":"building","region_id":"region-c","origin":"registry","created_at":"2026-01-01T00:00:00Z"}'
            )
            self.send_response(408 if target and not ready else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                disconnected.set()

        def log_message(self, format: str, *args: str) -> None:
            return

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            yield Peer(f"http://127.0.0.1:{server.server_port}", requests, disconnected)
        finally:
            release.set()
            server.shutdown()
            thread.join()


@pytest.fixture(params=[False, True], ids=["async", "generated-sync"])
def sync(request: pytest.FixtureRequest) -> bool:
    return bool(request.param)


@contextmanager
def sync_service(url: str, root_timeout: float | None) -> Iterator[ImagesService]:
    bridge = SyncBridge()

    async def make_transport() -> AsyncTransport:
        return AsyncTransport(url, "offline", request_timeout=root_timeout)

    transport = bridge.call(make_transport)
    try:
        yield ImagesService(AsyncImagesService(transport), bridge)
    finally:
        bridge.close(transport.close)


@pytest.fixture
async def invoke_pull(sync: bool) -> AsyncIterator[Pull]:
    # The real async implementation runs on either the caller or bridge loop.
    async def invoke(peer: Peer, budget: float | None, root_timeout: float | None = 1) -> Image:
        if sync:

            def pull() -> Image:
                with sync_service(peer.url, root_timeout) as service:
                    return service.pull("alpine", timeout=budget)

            return await anyio.to_thread.run_sync(pull)
        else:
            transport = AsyncTransport(peer.url, "offline", request_timeout=root_timeout)
            try:
                return await AsyncImagesService(transport).pull("alpine", timeout=budget)
            finally:
                await transport.close()

    yield invoke


@pytest.mark.anyio
@pytest.mark.parametrize("phase", [1, 2, 3], ids=["post", "initial-get", "poll"])
async def test_pull_cancels_request_when_deadline_expires(invoke_pull: Pull, phase: int) -> None:
    # Given a real HTTP peer which never answers this phase.
    with pull_peer(phase, None) as peer:
        # When the end-to-end budget expires. The budget comfortably covers
        # connecting and sending under a loaded parallel run (the earlier
        # phases answer at once, and the first poll follows the initial read
        # without a pause), so the deadline cancels the request in flight.
        budget = 1.0
        started = anyio.current_time()
        with anyio.fail_after(budget + 2), pytest.raises(RequestTimeoutError) as error:
            await invoke_pull(peer, budget, 5)
        # Then every phase has the same typed error and the socket is cancelled.
        assert anyio.current_time() - started < budget + 2
        assert str(error.value) == "image pull did not finish before timeout"
        assert await anyio.to_thread.run_sync(peer.disconnected.wait, 2)
        assert len(peer.requests) == phase


@pytest.mark.anyio
@pytest.mark.parametrize("phase", [1, 2, 3], ids=["post", "initial-get", "poll"])
@pytest.mark.parametrize("late", [False, True], ids=["early-408", "late-408"])
async def test_pull_preserves_408_only_when_before_deadline(
    invoke_pull: Pull, phase: int, late: bool
) -> None:
    # Given an HTTP 408 arriving well before or well after the pull deadline
    # (a budget that covers connecting under load).
    with pull_peer(phase, 2.0 if late else 0) as peer:
        # When pulling through the real transport.
        with pytest.raises(RequestTimeoutError) as error:
            await invoke_pull(peer, 1.0, 5)
        # Then only actual deadline exhaustion replaces the server message.
        assert str(error.value) == (
            "image pull did not finish before timeout" if late else "fixture request timeout"
        )


@pytest.mark.anyio
@pytest.mark.parametrize("phase", [1, 2], ids=["post", "initial-get"])
async def test_pull_preserves_root_timeout_when_before_deadline(invoke_pull: Pull, phase: int) -> None:
    # Given a shorter independent root timeout and a stalled HTTP peer.
    with pull_peer(phase, None) as peer:
        # When the root timeout expires before the overall pull budget.
        with pytest.raises(ClientTimeoutError) as error:
            await invoke_pull(peer, 3, 0.2)
        # Then it remains an independent transport error, not pull exhaustion.
        assert str(error.value) == "operation timed out"


@pytest.mark.anyio
@pytest.mark.parametrize("budget", [None, 0, 1])
async def test_pull_returns_ready_when_poll_outlasts_root_timeout(
    invoke_pull: Pull, budget: float | None
) -> None:
    # Given a real readiness poll that lasts longer than the root request timeout.
    with pull_peer(3, 0.5, ready=True) as peer:
        # When pulling with an unbounded or finite readiness budget.
        with anyio.fail_after(5):
            image = await invoke_pull(peer, 3 if budget == 1 else budget, 0.2)
        # Then readiness wins over the shorter per-request timeout.
        assert image.status == "ready"


@pytest.mark.anyio
async def test_pull_uses_whole_deadline_when_poll_never_answers(invoke_pull: Pull) -> None:
    # Given a stalled readiness poll with a shorter root request timeout.
    with pull_peer(3, None) as peer:
        started = anyio.current_time()
        # When the one-second pull budget expires.
        with anyio.fail_after(3), pytest.raises(RequestTimeoutError) as error:
            await invoke_pull(peer, 1, 0.2)
        # Then exhaustion, not the root timeout, supplies the error and timing.
        assert str(error.value) == "image pull did not finish before timeout"
        assert 0.9 <= anyio.current_time() - started < 3


@pytest.mark.anyio
async def test_pull_returns_ready_when_scaled_default_timeout_would_expire(invoke_pull: Pull) -> None:
    # Given a 30-second request timeout, a readiness answer after 35 s and a
    # pull budget well above that, scaled down for both entrypoints.
    with pull_peer(3, 1.4, ready=True) as peer:
        # When readiness arrives after the request timeout but within the pull deadline.
        image = await invoke_pull(peer, 3, 1.2)
        # Then the import completes rather than raising ClientTimeoutError.
        assert image.status == "ready"
