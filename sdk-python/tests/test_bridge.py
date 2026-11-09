# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Behavioural tests for the synchronous bridge.

These pin the properties that make the derived sync surface safe rather than
merely working: cancellation actually cancels, streams release their
connection, and a forked child fails loudly instead of deadlocking.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import AsyncIterator, ClassVar, Iterator

import anyio
import httpx
import pytest

from daytona.v2._bridge import BridgeError, SyncBridge
from daytona.v2._core import RequestSpec
from daytona.v2._transport import AsyncResponseStream, AsyncTransport
from daytona.v2.errors import ConnectionError as SdkConnectionError
from daytona.v2.errors import DaytonaError, NotFoundError

ROOT = Path(__file__).resolve().parents[1]

requires_fork = pytest.mark.skipif(not hasattr(os, "fork"), reason="fork() unavailable")
requires_sigint = pytest.mark.skipif(sys.platform == "win32", reason="SIGINT delivery differs on Windows")


@contextlib.contextmanager
def interrupt_after(delay: float) -> Iterator[None]:
    """Fire one SIGINT, and never let it escape into pytest.

    These tests signal the whole process. If the interrupt lands anywhere but
    inside the bridge call — a scheduling slip, or a change that stops the
    call blocking — an uncontained KeyboardInterrupt would abort the entire
    pytest session and destroy every other test's result.
    """
    timer = threading.Timer(delay, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.daemon = True
    timer.start()
    try:
        yield
    except KeyboardInterrupt:
        pytest.fail("KeyboardInterrupt escaped the bridge call")
    finally:
        timer.cancel()


@pytest.fixture
def bridge() -> Iterator[SyncBridge]:
    instance = SyncBridge()
    try:
        yield instance
    finally:
        instance.close()


async def _add(x: int, y: int) -> int:
    await asyncio.sleep(0)
    return x + y


def test_call_returns_the_coroutine_result(bridge: SyncBridge) -> None:
    assert bridge.call(lambda: _add(2, 3)) == 5


def test_call_preserves_exception_type_and_frame(bridge: SyncBridge) -> None:
    class Boom(Exception):
        pass

    async def boom() -> None:
        raise Boom("kaboom")

    with pytest.raises(Boom, match="kaboom") as caught:
        bridge.call(boom)

    frames = [frame.name for frame in __import__("traceback").extract_tb(caught.value.__traceback__)]
    assert "boom" in frames


def test_loop_thread_starts_on_first_call_not_construction() -> None:
    instance = SyncBridge()
    try:
        before = threading.active_count()
        instance.call(lambda: _add(1, 1))
        assert threading.active_count() > before
    finally:
        instance.close()


def test_all_calls_share_one_loop(bridge: SyncBridge) -> None:
    async def loop_id() -> int:
        return id(asyncio.get_running_loop())

    assert len({bridge.call(loop_id) for _ in range(5)}) == 1


def test_concurrent_sync_callers_share_the_bridge(bridge: SyncBridge) -> None:
    results: list[int] = []

    def worker(n: int) -> None:
        results.append(bridge.call(lambda: _add(n, n)))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [0, 2, 4, 6, 8, 10, 12, 14]


def test_calling_from_inside_a_running_loop_is_refused(bridge: SyncBridge) -> None:
    async def from_loop() -> str:
        with pytest.raises(BridgeError, match="running event loop") as caught:
            bridge.call(lambda: _add(1, 1))
        return str(caught.value)

    assert "AsyncDaytona" in asyncio.run(from_loop())


def test_async_iterator_drains_as_a_sync_iterator(bridge: SyncBridge) -> None:
    async def counter(n: int) -> AsyncIterator[int]:
        for i in range(n):
            yield i

    assert list(bridge.iterate(counter(5))) == [0, 1, 2, 3, 4]


def test_leaving_an_iterator_early_closes_the_async_generator(bridge: SyncBridge) -> None:
    finalized: list[bool] = []

    async def endless() -> AsyncIterator[int]:
        try:
            value = 0
            while True:
                yield value
                value += 1
        finally:
            finalized.append(True)

    iterator = bridge.iterate(endless())
    assert [next(iterator), next(iterator)] == [0, 1]
    iterator.close()

    assert finalized == [True], "async generator was not finalized; its connection would leak"


def test_context_propagates_into_the_loop_thread(bridge: SyncBridge) -> None:
    trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id")
    trace_id.set("abc123")

    async def read() -> str | None:
        try:
            return trace_id.get()
        except LookupError:
            return None

    assert bridge.call(read) == "abc123"


def test_close_is_idempotent_and_blocks_further_use() -> None:
    instance = SyncBridge()
    instance.call(lambda: _add(1, 1))
    instance.close()
    instance.close()

    with pytest.raises(BridgeError, match="closed"):
        instance.call(lambda: _add(1, 1))


def test_close_returns_promptly_while_an_iterator_is_open() -> None:
    instance = SyncBridge()

    async def endless() -> AsyncIterator[int]:
        value = 0
        while True:
            await asyncio.sleep(0.01)
            yield value
            value += 1

    iterator = instance.iterate(endless())
    next(iterator)

    started = time.monotonic()
    instance.close()
    assert time.monotonic() - started < 5.0


@requires_sigint
def test_interrupt_cancels_the_running_coroutine_promptly() -> None:
    instance = SyncBridge()
    observed: dict[str, bool] = {}

    async def long_op() -> None:
        try:
            await asyncio.sleep(30)
            observed["completed"] = True
        except asyncio.CancelledError:
            observed["cancelled"] = True
            raise

    started = time.monotonic()
    try:
        with interrupt_after(0.3), pytest.raises(KeyboardInterrupt):
            instance.call(long_op)
        elapsed = time.monotonic() - started

        # A naive wait on anyio's future never observes the cancellation and
        # would stall here for the full settle budget.
        assert elapsed < 3.0, f"interrupt took {elapsed:.2f}s to surface"
        assert observed.get("cancelled") is True
        assert "completed" not in observed
        assert instance.call(lambda: _add(20, 22)) == 42
    finally:
        instance.close()


@requires_sigint
def test_interrupt_runs_synchronous_cleanup_and_terminates_the_task() -> None:
    """What the interrupt path actually guarantees.

    Cancellation propagates *into* the operation, so a `finally` that awaits is
    itself cancelled partway through. The bridge guarantees the task has
    terminated before the caller unwinds — not that its async teardown
    completed. Teardown that must survive an interrupt shields itself; see
    test_stream_release_survives_cancellation.
    """
    instance = SyncBridge()
    trace: list[str] = []

    async def operation() -> None:
        try:
            await asyncio.sleep(30)
        finally:
            trace.append("entered")
            try:
                await asyncio.sleep(0.2)
                trace.append("completed")
            except asyncio.CancelledError:
                trace.append("cut-off")
                raise

    started = time.monotonic()
    try:
        with interrupt_after(0.3), pytest.raises(KeyboardInterrupt):
            instance.call(operation)
        elapsed = time.monotonic() - started

        # Bound the wall clock so a bridge that stops cancelling fails here
        # rather than by running the operation's full sleep.
        assert elapsed < 3.0, f"interrupt took {elapsed:.2f}s; the task was not cancelled"

        time.sleep(0.5)
        assert trace[0] == "entered", "the operation's cleanup never ran at all"
        assert trace[1] == "cut-off", (
            "an awaiting finalizer completed; if cancellation no longer propagates into "
            "cleanup, the comment in _bridge.call() and this test are both out of date"
        )
        assert instance.call(lambda: _add(1, 1)) == 2
    finally:
        instance.close()


def test_stream_release_survives_cancellation() -> None:
    """The release that must not be skipped when a reader is cancelled.

    A stream is closed from its reader's `finally`. Under an anyio cancel
    scope — which is what the bridge cancels through — an unshielded await
    there is cancelled with the reader, so the response is never released and
    an open connection goes back into the shared pool.
    """
    released: list[str] = []

    class _Stream:
        async def __aexit__(self, *args: object) -> None:
            await anyio.sleep(0.2)
            released.append("released")

    async def scenario() -> None:
        stream = AsyncResponseStream(_Stream(), httpx.Response(200))  # type: ignore[arg-type]

        async def reader() -> None:
            try:
                await anyio.sleep(30)
            finally:
                await stream.close()

        with anyio.CancelScope() as scope:
            async with anyio.create_task_group() as task_group:
                task_group.start_soon(reader)
                await anyio.sleep(0.05)
                scope.cancel()

    asyncio.run(scenario())

    assert released == ["released"], "cancelled reader skipped the release; the connection leaks"


def test_close_cannot_interleave_with_task_submission() -> None:
    """Teardown is serialised against submission.

    Without holding the lifecycle lock across submission, close() can stop the
    portal in the window between a caller acquiring it and handing it the task,
    and the task is then submitted to a dead portal. The observable invariant
    is that close() cannot make progress while a submission is in flight.
    """
    instance = SyncBridge()
    instance.call(lambda: _add(0, 0))
    portal = instance._portal
    assert portal is not None

    submission_started = threading.Event()
    allow_submission = threading.Event()
    original_submit = portal.start_task_soon

    def blocking_submit(func: object, *args: object, **kwargs: object) -> object:
        # Only stall the caller's own task. close() submits through this same
        # portal to stop itself, and intercepting that would block teardown for
        # reasons unrelated to the lock this test is about.
        if getattr(func, "__name__", "") == "run_and_signal":
            submission_started.set()
            allow_submission.wait(10)
        return original_submit(func, *args, **kwargs)  # type: ignore[arg-type]

    portal.start_task_soon = blocking_submit  # type: ignore[method-assign]

    closed_at: list[float] = []
    caller_failures: list[BaseException] = []

    def caller() -> None:
        try:
            instance.call(lambda: _add(1, 1))
        except BaseException as exc:
            caller_failures.append(exc)

    def closer() -> None:
        submission_started.wait(10)
        instance.close()
        closed_at.append(time.monotonic())

    threads = [threading.Thread(target=caller), threading.Thread(target=closer)]
    for thread in threads:
        thread.start()

    try:
        submission_started.wait(10)
        time.sleep(0.5)
        assert not closed_at, "close() tore the portal down while a submission was in flight"
    finally:
        # A failed assertion must not strand the caller thread on the gate.
        allow_submission.set()
        for thread in threads:
            thread.join(timeout=10)
        instance.close()

    assert not caller_failures, f"submission raced teardown: {caller_failures}"


def test_interrupt_warns_when_a_cancelled_task_outlives_the_settle_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("daytona.v2._bridge._CANCEL_SETTLE_SECONDS", 0.2)
    instance = SyncBridge()

    async def stubborn() -> None:
        try:
            await asyncio.sleep(30)
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.sleep(1.0)

    try:
        with (
            interrupt_after(0.3),
            pytest.warns(RuntimeWarning, match="still holding its resources"),
            pytest.raises(KeyboardInterrupt),
        ):
            instance.call(stubborn)
    finally:
        instance.close()


@requires_fork
def test_forked_child_reports_instead_of_deadlocking() -> None:
    instance = SyncBridge()
    instance.call(lambda: _add(1, 1))

    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child process
        os.close(read_fd)
        try:
            try:
                instance.call(lambda: _add(1, 1))
                os.write(write_fd, b"NO-ERROR")
            except BridgeError as exc:
                os.write(write_fd, str(exc).encode()[:200])
            except BaseException as exc:
                os.write(write_fd, f"WRONG {type(exc).__name__}".encode()[:200])
        finally:
            os.close(write_fd)
            os._exit(0)

    os.close(write_fd)
    os.set_blocking(read_fd, False)
    deadline, message = time.monotonic() + 10.0, b""
    while time.monotonic() < deadline and not message:
        try:
            message = os.read(read_fd, 400)
        except BlockingIOError:
            time.sleep(0.02)
    os.close(read_fd)
    if not message:
        os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)

    assert b"forked child" in message, f"child did not report cleanly: {message!r}"
    assert instance.call(lambda: _add(7, 7)) == 14
    instance.close()


def _mock_transport(handler) -> AsyncTransport:
    return AsyncTransport(
        "http://api.invalid",
        "tok-root",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def test_typed_sdk_errors_survive_the_bridge(bridge: SyncBridge) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            headers={"content-type": "application/json"},
            json={"message": "nope", "code": "NOT_FOUND"},
        )

    transport = _mock_transport(handler)
    try:
        with pytest.raises(NotFoundError) as caught:
            bridge.call(lambda: transport.send(RequestSpec("GET", "/boom")))
        assert caught.value.status_code == 404
        assert isinstance(caught.value, DaytonaError)
    finally:
        bridge.call(transport.close)


def test_scoped_transports_isolate_credentials_over_one_pool(bridge: SyncBridge) -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, headers={"content-type": "application/json"}, json={"ok": True})

    transport = _mock_transport(handler)
    scoped = transport.scoped("http://api.invalid", "tok-sandbox")
    try:
        assert scoped._client is transport._client
        bridge.call(lambda: scoped.send(RequestSpec("GET", "/one")))
        bridge.call(lambda: transport.send(RequestSpec("GET", "/two")))
        assert seen == ["Bearer tok-sandbox", "Bearer tok-root"]
    finally:
        bridge.call(transport.close)


def test_closing_a_scoped_transport_leaves_the_shared_pool_usable(bridge: SyncBridge) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, json={"ok": True})

    transport = _mock_transport(handler)
    scoped = transport.scoped("http://api.invalid", "tok-sandbox")
    try:
        bridge.call(scoped.close)
        assert bridge.call(lambda: transport.send(RequestSpec("GET", "/still-works"))) == {"ok": True}
    finally:
        bridge.call(transport.close)


class _StreamingHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    aborted: ClassVar[list[bool]] = []

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        try:
            for index in range(200):
                chunk = f"data: line-{index}\n\n".encode()
                self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                self.wfile.flush()
                time.sleep(0.01)
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            type(self).aborted.append(True)


def test_streaming_release_reaches_the_server(bridge: SyncBridge) -> None:
    _StreamingHandler.aborted.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StreamingHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    transport = AsyncTransport(f"http://127.0.0.1:{server.server_address[1]}", "tok")

    try:
        received: list[str] = []
        for line in bridge.iterate(transport.stream_lines(RequestSpec("GET", "/stream"))):
            if line.startswith("data:"):
                received.append(line)
            if len(received) == 3:
                break

        assert received == ["data: line-0", "data: line-1", "data: line-2"]

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not _StreamingHandler.aborted:
            time.sleep(0.05)
        assert _StreamingHandler.aborted, "server never saw the disconnect; the connection leaked"
    finally:
        bridge.call(transport.close)
        server.shutdown()


def test_async_stream_close_does_not_tear_down_the_shared_connection() -> None:
    """Closing one async stream must preserve other streams on the connection.

    The sync stream shuts the TCP socket down to wake a thread parked in
    recv(); on a negotiated HTTP/2 link that also kills sibling streams. The
    async path must never do this — one sandbox ending its log stream would
    disconnect every other sandbox multiplexed onto the shared client.
    """
    shutdown_calls: list[int] = []

    class _Socket:
        def shutdown(self, how: int) -> None:
            shutdown_calls.append(how)

    class _NetworkStream:
        def get_extra_info(self, name: str) -> object:
            return _Socket() if name == "socket" else None

    exited: list[bool] = []

    class _Stream:
        async def __aexit__(self, *args: object) -> None:
            exited.append(True)

    response = httpx.Response(200, extensions={"network_stream": _NetworkStream()})
    stream = AsyncResponseStream(_Stream(), response)  # type: ignore[arg-type]

    asyncio.run(stream.close())

    assert exited == [True], "the response context was not released"
    assert shutdown_calls == [], "async close shut the shared socket down; that would tear down siblings"


def test_async_raw_request_helpers_mirror_the_sync_ones() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url.path)))
        return httpx.Response(200, headers={"content-type": "application/json"}, json={"ok": True})

    async def scenario() -> None:
        transport = _mock_transport(handler)
        try:
            await transport.get("/g")
            await transport.post("/p")
            await transport.put("/u")
            await transport.patch("/a")
            await transport.delete("/d")
        finally:
            await transport.close()

    asyncio.run(scenario())

    assert seen == [
        ("GET", "/g"),
        ("POST", "/p"),
        ("PUT", "/u"),
        ("PATCH", "/a"),
        ("DELETE", "/d"),
    ]


def test_async_raw_request_raises_typed_errors_before_returning() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            headers={"content-type": "application/json"},
            json={"message": "gone", "code": "NOT_FOUND"},
        )

    async def scenario() -> None:
        transport = _mock_transport(handler)
        try:
            with pytest.raises(NotFoundError):
                await transport.get("/missing")
        finally:
            await transport.close()

    asyncio.run(scenario())


def test_async_shared_transport_has_no_base_url() -> None:
    transport = AsyncTransport.shared()
    try:
        assert transport.base_url is None
        assert transport.token is None
    finally:
        asyncio.run(transport.close())


def test_async_open_stream_surfaces_error_status_and_releases_the_stream() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"content-type": "application/json"},
            json={"message": "denied", "code": "FORBIDDEN"},
        )

    async def scenario() -> None:
        transport = _mock_transport(handler)
        try:
            with pytest.raises(DaytonaError) as caught:
                await transport.open_stream(RequestSpec("GET", "/stream"))
            assert caught.value.status_code == 403
        finally:
            await transport.close()

    asyncio.run(scenario())


def test_async_stream_translates_transport_failures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async def scenario() -> None:
        transport = _mock_transport(handler)
        try:
            with pytest.raises(SdkConnectionError):
                await transport.open_stream(RequestSpec("GET", "/stream"))
        finally:
            await transport.close()

    asyncio.run(scenario())


def test_bridge_works_as_a_context_manager() -> None:
    with SyncBridge() as instance:
        assert instance.call(lambda: _add(1, 2)) == 3

    with pytest.raises(BridgeError, match="closed"):
        instance.call(lambda: _add(1, 2))


def test_native_async_callers_need_no_bridge() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/json"}, json={"path": str(request.url.path)}
        )

    async def scenario() -> list[object]:
        transport = _mock_transport(handler)
        try:
            return await asyncio.gather(*[transport.send(RequestSpec("GET", f"/p{i}")) for i in range(20)])
        finally:
            await transport.close()

    results = asyncio.run(scenario())
    assert len(results) == 20
    assert json.dumps(results[0]) == '{"path": "/p0"}'


def test_unclosed_sync_client_does_not_hang_interpreter_exit() -> None:
    """A script that never calls close() must still exit promptly and cleanly.

    Without the atexit hook, Python finalizes the un-exited portal context
    manager during shutdown and its stop() blocks forever on a loop thread
    that is already being torn down.
    """
    script = f"""
import sys
sys.path.insert(0, {str(ROOT / "src")!r})
from daytona.v2._sync import Daytona
from daytona.v2.types import DaytonaConfig
client = Daytona(DaytonaConfig(api_key="k"))
pool = client._impl._api._client
orig = pool.aclose
async def spy():
    print("POOL-RELEASED"); await orig()
pool.aclose = spy
async def touch(): return 1
client._bridge.call(touch)
print("EXITING-WITHOUT-CLOSE")
"""
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=15, check=False
    )
    elapsed = time.monotonic() - started
    assert result.returncode == 0, result.stderr[-800:]
    assert "EXITING-WITHOUT-CLOSE" in result.stdout
    assert "POOL-RELEASED" in result.stdout, "SDK-owned HTTP pool was not released at exit"
    assert elapsed < 10, f"exit took {elapsed:.1f}s"


def test_close_from_the_loop_thread_shuts_the_loop_down_without_raising() -> None:
    """A sync callback the loop is running may call client.close(). That
    cannot join the loop's own thread; it must mark the bridge closed, hand
    the shutdown off, and the loop thread must actually end."""
    instance = SyncBridge()

    async def close_from_inside() -> str:
        instance.close()
        return "returned"

    assert instance.call(close_from_inside) == "returned"
    assert instance.closed

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any("portal" in t.name for t in threading.enumerate()):
        time.sleep(0.02)
    assert not any("portal" in t.name for t in threading.enumerate()), "loop thread left alive"

    with pytest.raises(BridgeError, match="closed"):
        instance.call(lambda: _add(1, 1))


def test_close_with_an_open_iterator_finalizes_its_async_generator() -> None:
    """Pins that portal shutdown cancels a parked iterator's task so its
    `finally` runs — closing the bridge never strands an SDK stream."""
    instance = SyncBridge()
    finalized: list[bool] = []

    async def endless() -> AsyncIterator[int]:
        try:
            value = 0
            while True:
                await asyncio.sleep(0.001)
                yield value
                value += 1
        finally:
            finalized.append(True)

    iterator = instance.iterate(endless())
    next(iterator)
    next(iterator)
    instance.close()
    assert finalized == [True]


def test_close_runs_the_finalizer_only_after_in_flight_calls_finish() -> None:
    """The owner's finalizer closes HTTP pools; a request still using one
    must never see it close underneath it."""
    instance = SyncBridge()
    order: list[str] = []
    in_flight = threading.Event()  # set by the call itself: no timing guess

    async def slow_call() -> None:
        in_flight.set()
        await asyncio.sleep(0.4)
        order.append("call done")

    async def finalizer() -> None:
        order.append("finalizer ran")

    caller = threading.Thread(target=lambda: instance.call(slow_call))
    caller.start()
    assert in_flight.wait(5), "the call never started"
    instance.close(finalizer)
    caller.join(timeout=5)

    assert order == ["call done", "finalizer ran"]


def test_close_on_a_never_started_bridge_still_runs_the_finalizer() -> None:
    """An owner that created resources eagerly but never made a call must not
    leak them because the loop never spun up."""
    instance = SyncBridge()
    ran: list[bool] = []

    async def finalizer() -> None:
        ran.append(True)

    instance.close(finalizer)
    assert ran == [True]


def test_close_on_a_never_started_bridge_inside_a_running_loop_still_runs_the_finalizer() -> None:
    """`with Daytona() as d: d.v2` inside async code. asyncio.run cannot
    nest, so the finalizer runs on a helper thread: it runs, the caller's loop
    is not blocked, and no coroutine is left un-awaited."""
    import warnings

    instance = SyncBridge()
    ran = threading.Event()

    async def finalizer() -> None:
        ran.set()

    async def main() -> None:
        instance.close(finalizer)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        asyncio.run(main())
        assert ran.wait(5), "the finalizer never ran"
    assert not [w for w in caught if issubclass(w.category, RuntimeWarning)], [str(w.message) for w in caught]


def test_close_from_an_unrelated_event_loop_finalizes_on_the_bridge_loop() -> None:
    """A user's own asyncio app closing a used Daytona: the finalizer must
    run on the BRIDGE loop (where the pools live), not be scheduled onto the
    caller's loop, and must not block that loop."""
    instance = SyncBridge()
    instance.call(lambda: _add(1, 1))
    bridge_loop = instance._loop
    ran_on: list[object] = []

    async def finalizer() -> None:
        ran_on.append(asyncio.get_running_loop())

    async def user_app() -> float:
        started = time.monotonic()
        instance.close(finalizer)
        blocked_for = time.monotonic() - started
        await asyncio.sleep(0.5)
        return blocked_for

    started = time.monotonic()
    blocked_for = asyncio.run(user_app())
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and any("portal" in t.name for t in threading.enumerate()):
        time.sleep(0.02)
    shutdown_took = time.monotonic() - started

    assert ran_on and ran_on[0] is bridge_loop, "finalizer ran on the wrong loop"
    assert blocked_for < 0.2, f"close() blocked the caller's loop for {blocked_for:.2f}s"
    # Mistaking the caller's loop for the bridge's own makes shutdown wait for
    # an in-flight call that does not exist, until the settle window expires.
    assert shutdown_took < 2.0, (
        f"loop thread took {shutdown_took:.2f}s to end: caller's loop mistaken for the bridge loop"
    )


def test_in_loop_close_waits_for_other_in_flight_calls_before_finalizing() -> None:
    """close() from a callback the loop is running must still drain the OTHER
    in-flight calls before the finalizer closes the pools they may be using —
    while excluding the closing call itself, which finishes on its own."""
    instance = SyncBridge()
    order: list[str] = []
    other_started = threading.Event()

    async def other() -> None:
        other_started.set()
        await asyncio.sleep(0.4)
        order.append("other done")

    async def finalizer() -> None:
        order.append("finalizer")

    async def callback_on_loop() -> str:
        instance.close(finalizer)
        return "returned"

    other_thread = threading.Thread(target=lambda: instance.call(other))
    other_thread.start()
    assert other_started.wait(5)
    assert instance.call(callback_on_loop) == "returned"
    other_thread.join(timeout=5)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "finalizer" not in order:
        time.sleep(0.02)
    assert order == ["other done", "finalizer"]


def test_a_hung_finalizer_cannot_hold_shutdown_hostage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("daytona.v2._bridge._FINALIZER_GRACE_SECONDS", 0.3)
    instance = SyncBridge()
    instance.call(lambda: _add(1, 1))

    async def hangs() -> None:
        await asyncio.sleep(60)

    started = time.monotonic()
    instance.close(hangs)
    elapsed = time.monotonic() - started

    assert elapsed < 3.0, f"close() waited {elapsed:.1f}s on a hung finalizer"
    assert not any("portal" in t.name for t in threading.enumerate())


def test_in_loop_shutdown_waits_for_a_slow_finalizer_instead_of_cancelling_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper thread that exits the portal after an in-loop close() must
    wait at least as long as the finalizer is allowed to run; a shorter wait
    cancels a finalizer that is still legitimately releasing resources."""
    monkeypatch.setattr("daytona.v2._bridge._CANCEL_SETTLE_SECONDS", 0.1)
    monkeypatch.setattr("daytona.v2._bridge._FINALIZER_GRACE_SECONDS", 2.0)
    instance = SyncBridge()
    finished: list[bool] = []

    async def slow_finalizer() -> None:
        await asyncio.sleep(0.6)  # longer than the settle window, within the grace
        finished.append(True)

    async def callback_on_loop() -> str:
        instance.close(slow_finalizer)
        return "returned"

    assert instance.call(callback_on_loop) == "returned"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any("portal" in t.name for t in threading.enumerate()):
        time.sleep(0.02)

    assert finished == [True], "portal exit cancelled a finalizer that was still within its grace window"


@pytest.mark.parametrize("method", ["iter_bytes", "iter_lines"])
def test_stopping_a_stream_early_does_not_record_a_span_error(method: str) -> None:
    finished: list[tuple[int | None, BaseException | None]] = []

    class _Span:
        def finish(self, *, status_code: int | None = None, error: BaseException | None = None) -> None:
            finished.append((status_code, error))

    class _Stream:
        async def __aexit__(self, *args: object) -> None:
            return None

    async def scenario() -> None:
        response = httpx.Response(200, stream=httpx.ByteStream(b"one\ntwo\nthree\n"))
        stream = AsyncResponseStream(_Stream(), response, span=_Span())  # type: ignore[arg-type]
        gen = getattr(stream, method)()
        await gen.__anext__()
        await gen.aclose()

    asyncio.run(scenario())

    assert finished, "the span was never finished"
    assert all(error is None for _, error in finished), finished


def test_on_a_bridge_loop_is_safe_while_other_threads_create_clients() -> None:
    """Copying the live-bridge set while other threads register (and drop)
    bridges must never raise 'Set changed size during iteration'."""
    import asyncio
    import threading
    import time

    from daytona.v2._bridge import SyncBridge, on_a_bridge_loop

    stop = threading.Event()
    errors: list[BaseException] = []

    def create() -> None:
        try:
            while not stop.is_set():
                SyncBridge()  # registers, then is dropped
        except BaseException as exc:
            errors.append(exc)

    async def check() -> int:
        checks = 0
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            assert on_a_bridge_loop() is False
            checks += 1
        return checks

    creators = [threading.Thread(target=create, daemon=True) for _ in range(4)]
    for thread in creators:
        thread.start()
    try:
        checks = asyncio.run(check())
    finally:
        stop.set()
        for thread in creators:
            thread.join(5)
    assert errors == []
    assert checks > 0


def test_a_post_racing_close_runs_or_reports_the_closed_bridge() -> None:
    """post() either schedules on a running loop or raises BridgeError, never
    a raw RuntimeError from a loop close() already stopped."""
    import threading

    from daytona.v2._bridge import BridgeError, SyncBridge

    async def noop() -> None:
        return None

    for _ in range(30):
        bridge = SyncBridge()
        bridge.call(noop)
        outcomes: list[str] = []

        def post_until_closed(bridge: SyncBridge = bridge, outcomes: list[str] = outcomes) -> None:
            while True:
                try:
                    bridge.post(lambda: None)
                except BridgeError:
                    outcomes.append("closed")
                    return
                except BaseException as exc:
                    outcomes.append(repr(exc))
                    return

        poster = threading.Thread(target=post_until_closed, daemon=True)
        poster.start()
        bridge.close()
        poster.join(5)
        assert outcomes == ["closed"]


def test_post_schedules_while_holding_the_lock_close_takes() -> None:
    from daytona.v2._bridge import SyncBridge

    async def noop() -> None:
        return None

    bridge = SyncBridge()
    try:
        bridge.call(noop)
        loop = bridge._loop
        assert loop is not None
        held: list[bool] = []
        schedule = loop.call_soon_threadsafe

        def recording(callback: object, *args: object) -> object:
            held.append(bridge._lock.locked())
            return schedule(callback, *args)  # type: ignore[arg-type]

        loop.call_soon_threadsafe = recording  # type: ignore[method-assign]
        try:
            bridge.post(lambda: None)
        finally:
            del loop.call_soon_threadsafe
        assert held == [True]
    finally:
        bridge.close()


def test_registering_and_copying_live_bridges_share_one_lock() -> None:
    import asyncio
    import threading

    from daytona.v2 import _bridge

    done: list[str] = []

    def snapshot() -> None:
        async def check() -> None:
            _bridge.on_a_bridge_loop()

        asyncio.run(check())
        done.append("snapshot")

    def register() -> None:
        _bridge.SyncBridge()
        done.append("register")

    with _bridge._LIVE_BRIDGES_LOCK:
        workers = [
            threading.Thread(target=snapshot, daemon=True),
            threading.Thread(target=register, daemon=True),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(0.2)
        assert done == []
    for worker in workers:
        worker.join(5)
    assert sorted(done) == ["register", "snapshot"]


def test_a_post_on_the_loop_thread_after_close_raises_bridge_error() -> None:
    from daytona.v2._bridge import BridgeError, SyncBridge

    bridge = SyncBridge()
    ran: list[str] = []
    outcome: list[str] = []

    async def post_after_close() -> None:
        bridge.close()  # from a call on the loop, which close() supports
        try:
            bridge.post(lambda: ran.append("ran"))
        except BridgeError as error:
            outcome.append(str(error))

    bridge.call(post_after_close)
    assert bridge.closed
    assert ran == []
    assert outcome == ["Daytona is closed"]


def test_a_post_on_the_loop_thread_runs_the_operation_at_once() -> None:
    from daytona.v2._bridge import SyncBridge

    bridge = SyncBridge()
    ran: list[str] = []

    async def post_on_loop() -> None:
        bridge.post(lambda: ran.append("ran"))
        assert ran == ["ran"]

    try:
        bridge.call(post_on_loop)
    finally:
        bridge.close()
    assert ran == ["ran"]


def test_a_post_from_another_thread_after_close_raises_bridge_error() -> None:
    from daytona.v2._bridge import BridgeError, SyncBridge

    async def noop() -> None:
        return None

    bridge = SyncBridge()
    bridge.call(noop)
    bridge.close()
    with pytest.raises(BridgeError, match="is closed"):
        bridge.post(lambda: None)
