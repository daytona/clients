# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""File transfers are idle-bounded, and buffered response bodies are capped.

Every test runs against a real HTTP server on a socket: httpx applies its
per-phase timeouts only there (respx never does). The TypeScript SDK's
tests/transfer-bounds.test.ts holds its transports to the same rules.
"""

from __future__ import annotations

import contextlib
import http.server
import socket
import threading
import time
from pathlib import Path
from typing import Callable, Iterator

import anyio
import pytest
from _handles import lazy_sandbox

from daytona import AsyncDaytona, Daytona, DaytonaConfig
from daytona.v2 import BadRequestError, ClientTimeoutError, NotFoundError, PayloadTooLargeError
from daytona.v2._core import RequestSpec
from daytona.v2._transport import AsyncTransport

IDLE = 0.5
STALL = 5.0

Handler = Callable[[http.server.BaseHTTPRequestHandler], None]


def _send_head(request: http.server.BaseHTTPRequestHandler, status: int = 200, **headers: str) -> None:
    request.send_response(status)
    for name, value in headers.items():
        request.send_header(name.replace("_", "-"), value)
    request.end_headers()
    request.wfile.flush()


def _body(request: http.server.BaseHTTPRequestHandler, payload: bytes, status: int = 200) -> None:
    _send_head(request, status, Content_Type="application/octet-stream", Content_Length=str(len(payload)))
    request.wfile.write(payload)


def _json(request: http.server.BaseHTTPRequestHandler, payload: bytes, status: int = 200) -> None:
    _send_head(request, status, Content_Type="application/json", Content_Length=str(len(payload)))
    request.wfile.write(payload)


def _drain(request: http.server.BaseHTTPRequestHandler) -> None:
    length = int(request.headers.get("Content-Length") or 0)
    if length:
        request.rfile.read(length)


@contextlib.contextmanager
def _server(routes: dict[str, Handler]) -> Iterator[str]:
    """A threaded HTTP/1.1 server; a route is matched on the path's start."""

    class RequestHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: object) -> None:
            pass

        def _dispatch(self) -> None:
            for prefix, handle in routes.items():
                if self.path.startswith(prefix):
                    try:
                        handle(self)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
            _drain(self)
            _json(self, b'{"code":"NOT_FOUND","message":"no route"}', 404)

        do_GET = do_POST = do_PUT = do_DELETE = _dispatch

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RequestHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _hang_up(request: http.server.BaseHTTPRequestHandler) -> None:
    """End a stall: close the connection, so a client with no idle bound fails
    (with a connection error, not the timeout these tests expect) instead of
    hanging the suite."""
    request.close_connection = True
    with contextlib.suppress(OSError):
        request.connection.shutdown(socket.SHUT_RDWR)


def _stall_before_headers(request: http.server.BaseHTTPRequestHandler) -> None:
    _drain(request)
    time.sleep(STALL)
    _hang_up(request)


def _stall_mid_body(request: http.server.BaseHTTPRequestHandler) -> None:
    _drain(request)
    _send_head(request, Content_Type="application/octet-stream", Content_Length="2000")
    request.wfile.write(b"x" * 1000)
    request.wfile.flush()
    time.sleep(STALL)
    _hang_up(request)


def _stop_reading(request: http.server.BaseHTTPRequestHandler) -> None:
    # Reads nothing of the upload: the client's writes stall once the socket
    # buffers are full.
    time.sleep(STALL)
    _hang_up(request)


def _trickle(chunks: int, gap: float, chunk: bytes = b"x") -> Handler:
    def handle(request: http.server.BaseHTTPRequestHandler) -> None:
        _drain(request)
        _send_head(request, Content_Type="application/octet-stream", Content_Length=str(chunks * len(chunk)))
        for _ in range(chunks):
            time.sleep(gap)
            request.wfile.write(chunk)
            request.wfile.flush()

    return handle


def _late(delay: float, payload: bytes) -> Handler:
    def handle(request: http.server.BaseHTTPRequestHandler) -> None:
        _drain(request)
        time.sleep(delay)
        _body(request, payload)

    return handle


def _client(base: str, **config: object) -> AsyncDaytona:
    return AsyncDaytona(DaytonaConfig(api_key="k", **config))  # type: ignore[arg-type]


async def _timed(call: Callable[[], object]) -> float:
    started = time.monotonic()
    with pytest.raises(ClientTimeoutError) as caught:
        await call()  # type: ignore[misc]
    assert (caught.value.status_code, caught.value.code, caught.value.source) == (
        408,
        "REQUEST_TIMEOUT",
        "SDK",
    )
    return time.monotonic() - started


# --- idle bound --------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("route", ["stall-headers", "stall-body"])
async def test_a_stalled_download_fails_after_the_idle_bound(route: str, tmp_path: Path) -> None:
    handler = _stall_before_headers if route == "stall-headers" else _stall_mid_body
    with _server(
        {"/files/download": handler, "/computeruse/": handler, "/files/bulk-download": handler}
    ) as base:
        root = _client(base, request_timeout=IDLE)
        try:
            sandbox = lazy_sandbox(root.v2, "sbx", toolbox_url=base, access_token="t")
            for call in (
                lambda: sandbox.fs.read_bytes("/a"),
                lambda: sandbox.fs.read_many(["/a"]),
                lambda: sandbox.computer_use.recording.download("rec"),
                lambda: sandbox.fs.download_file("/a", tmp_path / "out.bin"),
            ):
                elapsed = await _timed(call)
                assert IDLE * 0.8 <= elapsed < STALL - 1
        finally:
            await root.close()


@pytest.mark.anyio
async def test_an_upload_the_server_stops_reading_fails_after_the_idle_bound(tmp_path: Path) -> None:
    big = b"x" * (64 << 20)
    local = tmp_path / "big.bin"
    local.write_bytes(big)
    with _server({"/files/": _stop_reading}) as base:
        root = _client(base, request_timeout=IDLE)
        try:
            sandbox = lazy_sandbox(root.v2, "sbx", toolbox_url=base, access_token="t")
            for call in (
                lambda: sandbox.fs.write_bytes("/a", big),
                lambda: sandbox.fs.write_many({"/a": big}),
                lambda: sandbox.fs.upload_file(local, "/a"),
            ):
                elapsed = await _timed(call)
                assert elapsed < STALL - 1
        finally:
            await root.close()


@pytest.mark.anyio
async def test_a_transfer_that_keeps_moving_bytes_outlasts_the_idle_bound(tmp_path: Path) -> None:
    # 12 bytes 0.2 s apart: 2.4 s in all, five times the idle bound.
    with _server({"/files/download": _trickle(12, 0.2), "/computeruse/": _trickle(12, 0.2)}) as base:
        root = _client(base, request_timeout=IDLE)
        try:
            sandbox = lazy_sandbox(root.v2, "sbx", toolbox_url=base, access_token="t")
            assert await sandbox.fs.read_bytes("/a") == b"x" * 12
            assert await sandbox.computer_use.recording.download("rec") == b"x" * 12
            await sandbox.fs.download_file("/a", tmp_path / "out.bin")
            assert (tmp_path / "out.bin").read_bytes() == b"x" * 12
        finally:
            await root.close()


@pytest.mark.anyio
async def test_request_timeout_zero_leaves_a_transfer_unbounded() -> None:
    # A stall twice as long as the idle bound of the other tests completes.
    with _server({"/files/download": _late(2 * IDLE + 0.5, b"done")}) as base:
        root = _client(base, request_timeout=0)
        try:
            sandbox = lazy_sandbox(root.v2, "sbx", toolbox_url=base, access_token="t")
            with anyio.fail_after(STALL):
                assert await sandbox.fs.read_bytes("/a") == b"done"
        finally:
            await root.close()


def test_the_sync_client_has_the_same_idle_bound(tmp_path: Path) -> None:
    with (
        _server({"/files/download": _stall_mid_body, "/files/": _stop_reading}) as base,
        Daytona(DaytonaConfig(api_key="k", request_timeout=IDLE)) as root,
    ):
        sandbox = lazy_sandbox(root.v2, "sbx", toolbox_url=base, access_token="t")
        for call in (
            lambda: sandbox.fs.read_bytes("/a"),
            lambda: sandbox.fs.download_file("/a", tmp_path / "out.bin"),
            lambda: sandbox.fs.write_bytes("/a", b"x" * (64 << 20)),
        ):
            started = time.monotonic()
            with pytest.raises(ClientTimeoutError):
                call()
            assert time.monotonic() - started < STALL - 1


# --- size cap ----------------------------------------------------------------


def _declared_then_slow(size: int) -> Handler:
    """Declares ``size`` bytes, then sends a byte every 0.5 s and hangs up after 3 s.

    A client that waits for the body fails late, with a connection error.
    """

    def handle(request: http.server.BaseHTTPRequestHandler) -> None:
        _drain(request)
        _send_head(request, Content_Type="application/octet-stream", Content_Length=str(size))
        for _ in range(6):
            time.sleep(0.5)
            request.wfile.write(b"x")
            request.wfile.flush()
        _hang_up(request)

    return handle


def _chunked(
    chunks: int, chunk: bytes, content_type: str = "application/octet-stream", status: int = 200
) -> Handler:
    def handle(request: http.server.BaseHTTPRequestHandler) -> None:
        _drain(request)
        _send_head(request, status, Content_Type=content_type, Transfer_Encoding="chunked")
        for _ in range(chunks):
            request.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            request.wfile.flush()
        request.wfile.write(b"0\r\n\r\n")

    return handle


def _assert_too_large(error: PayloadTooLargeError, limit: int) -> None:
    assert (error.status_code, error.code, error.source) == (413, "PAYLOAD_TOO_LARGE", "SDK")
    assert f"{limit}-byte" in str(error)
    assert "max_response_bytes" in str(error) and "download_file" in str(error)


@pytest.mark.anyio
async def test_a_declared_length_over_the_cap_is_refused_before_the_body_is_read() -> None:
    with _server({"/files/download": _declared_then_slow(10_000)}) as base:
        root = _client(base, max_response_bytes=1000)
        try:
            sandbox = lazy_sandbox(root.v2, "sbx", toolbox_url=base, access_token="t")
            started = time.monotonic()
            with pytest.raises(PayloadTooLargeError) as caught:
                await sandbox.fs.read_bytes("/a")
            # The body would take hours at that rate.
            assert time.monotonic() - started < 2
            _assert_too_large(caught.value, 1000)
        finally:
            await root.close()


@pytest.mark.anyio
async def test_a_chunked_body_is_refused_once_it_passes_the_cap() -> None:
    with _server({"/files/download": _chunked(10, b"y" * 512)}) as base:
        root = _client(base, max_response_bytes=2000)
        try:
            sandbox = lazy_sandbox(root.v2, "sbx", toolbox_url=base, access_token="t")
            with pytest.raises(PayloadTooLargeError) as caught:
                await sandbox.fs.read_bytes("/a")
            _assert_too_large(caught.value, 2000)
            # Exactly at the cap is fine.
            assert len(await sandbox.fs.read_bytes("/a", max_response_bytes=5120)) == 5120
        finally:
            await root.close()


@pytest.mark.anyio
async def test_the_per_call_option_raises_lowers_and_disables_the_cap() -> None:
    payload = b"z" * 5000
    multipart = (
        b'--b\r\nContent-Disposition: form-data; name="file"; filename="/a"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n" + payload + b"\r\n--b--\r\n"
    )

    def bulk(request: http.server.BaseHTTPRequestHandler) -> None:
        _drain(request)
        _send_head(
            request, Content_Type="multipart/form-data; boundary=b", Content_Length=str(len(multipart))
        )
        request.wfile.write(multipart)

    routes: dict[str, Handler] = {
        "/files/download": lambda r: (_drain(r), _body(r, payload)),  # type: ignore[func-returns-value]
        "/files/bulk-download": bulk,
        "/computeruse/": lambda r: (_drain(r), _body(r, payload)),  # type: ignore[func-returns-value]
    }
    with _server(routes) as base:
        small = _client(base, max_response_bytes=1000)
        large = _client(base, max_response_bytes=100_000)
        try:
            tight = lazy_sandbox(small.v2, "sbx", toolbox_url=base, access_token="t")
            roomy = lazy_sandbox(large.v2, "sbx", toolbox_url=base, access_token="t")
            # Raised above the client's cap, and disabled.
            assert await tight.fs.read_bytes("/a", max_response_bytes=10_000) == payload
            assert await tight.fs.read_text("/a", max_response_bytes=0) == payload.decode()
            assert (await tight.fs.read_many(["/a"], max_response_bytes=0)).files["/a"] == payload
            assert await tight.computer_use.recording.download("r", max_response_bytes=10_000) == payload
            with pytest.raises(PayloadTooLargeError):
                await tight.fs.read_bytes("/a")
            with pytest.raises(PayloadTooLargeError):
                await tight.fs.read_many(["/a"])
            # Lowered below it.
            assert await roomy.fs.read_bytes("/a") == payload
            for call in (
                lambda: roomy.fs.read_bytes("/a", max_response_bytes=100),
                lambda: roomy.fs.read_text("/a", max_response_bytes=100),
                lambda: roomy.fs.read_many(["/a"], max_response_bytes=100),
                lambda: roomy.computer_use.recording.download("r", max_response_bytes=100),
            ):
                with pytest.raises(PayloadTooLargeError) as caught:
                    await call()
                _assert_too_large(caught.value, 100)
        finally:
            await small.close()
            await large.close()


_INVALID_LIMITS: list[object] = [-1, 1.5, 10.0, True, "10", 2**53]


@pytest.mark.parametrize("bad", _INVALID_LIMITS)
def test_the_config_refuses_an_invalid_cap(bad: object) -> None:
    with pytest.raises(BadRequestError, match="max_response_bytes must be an integer from 0") as caught:
        _ = AsyncDaytona(DaytonaConfig(api_key="k", max_response_bytes=bad)).v2  # type: ignore[arg-type]
    assert (caught.value.status_code, caught.value.code, caught.value.source) == (400, "BAD_REQUEST", "SDK")


def test_the_config_takes_none_zero_and_the_safe_integer_maximum() -> None:
    assert AsyncDaytona(DaytonaConfig(api_key="k")).v2._api.max_response_bytes == 1 << 30  # type: ignore[attr-defined]
    for value in (0, 2**53 - 1, 1):
        client = AsyncDaytona(DaytonaConfig(api_key="k", max_response_bytes=value)).v2
        assert client._api.max_response_bytes == value  # type: ignore[attr-defined]
        assert client._toolbox.max_response_bytes == value  # type: ignore[attr-defined]


@pytest.mark.anyio
@pytest.mark.parametrize("bad", _INVALID_LIMITS)
async def test_the_per_call_option_is_validated_before_any_request(bad: object) -> None:
    hits: list[str] = []

    def record(request: http.server.BaseHTTPRequestHandler) -> None:
        hits.append(request.path)
        _drain(request)
        _body(request, b"ok")

    with _server({"/": record}) as base:
        root = _client(base)
        try:
            sandbox = lazy_sandbox(root.v2, "sbx", toolbox_url=base, access_token="t")
            for call in (
                lambda: sandbox.fs.read_bytes("/a", max_response_bytes=bad),  # type: ignore[arg-type]
                lambda: sandbox.fs.read_text("/a", max_response_bytes=bad),  # type: ignore[arg-type]
                lambda: sandbox.fs.read_many(["/a"], max_response_bytes=bad),  # type: ignore[arg-type]
                lambda: sandbox.computer_use.recording.download("r", max_response_bytes=bad),  # type: ignore[arg-type]
            ):
                with pytest.raises(BadRequestError, match="max_response_bytes must be an integer"):
                    await call()
        finally:
            await root.close()
    assert hits == []


@pytest.mark.anyio
async def test_a_json_response_over_a_small_client_cap_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox_json = b'{"id":"sbx","state":"started","pad":"' + b"p" * 4000 + b'"}'
    with _server({"/sandboxes": _chunked(1, sandbox_json, "application/json")}) as base:
        monkeypatch.setenv("DAYTONA_V2_API_URL", base)
        root = _client(base, max_response_bytes=1000)
        try:
            with pytest.raises(PayloadTooLargeError) as caught:
                await root.v2.get("sbx")
            _assert_too_large(caught.value, 1000)
        finally:
            await root.close()


@pytest.mark.anyio
async def test_an_error_status_with_an_oversized_body_keeps_its_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    big = b'{"code":"NOT_FOUND","message":"' + b"m" * 5000 + b'"}'
    routes: dict[str, Handler] = {
        "/sandboxes": lambda r: (_drain(r), _json(r, big, 404)),  # type: ignore[func-returns-value]
        "/files/download": lambda r: (_drain(r), _json(r, big, 404)),  # type: ignore[func-returns-value]
    }
    with _server(routes) as base:
        monkeypatch.setenv("DAYTONA_V2_API_URL", base)
        root = _client(base, max_response_bytes=1000)
        try:
            sandbox = lazy_sandbox(root.v2, "sbx", toolbox_url=base, access_token="t")
            for call in (lambda: root.v2.get("sbx"), lambda: sandbox.fs.read_bytes("/a")):
                with pytest.raises(NotFoundError) as caught:
                    await call()
                assert caught.value.status_code == 404
                assert "too large to read" in str(caught.value)
        finally:
            await root.close()


@pytest.mark.anyio
async def test_download_file_is_not_capped(tmp_path: Path) -> None:
    payload = b"d" * 50_000
    with _server({"/files/download": lambda r: (_drain(r), _body(r, payload))}) as base:  # type: ignore[func-returns-value]
        root = _client(base, max_response_bytes=1000)
        try:
            sandbox = lazy_sandbox(root.v2, "sbx", toolbox_url=base, access_token="t")
            await sandbox.fs.download_file("/a", tmp_path / "out.bin")
            assert (tmp_path / "out.bin").read_bytes() == payload
        finally:
            await root.close()


@pytest.mark.anyio
async def test_the_client_works_after_a_refused_response() -> None:
    calls: list[str] = []

    def handle(request: http.server.BaseHTTPRequestHandler) -> None:
        calls.append(request.path)
        _drain(request)
        _body(request, b"b" * (5000 if "big" in request.path else 10))

    with _server({"/files/download": handle}) as base:
        transport = AsyncTransport(base, "t", request_timeout=5, max_response_bytes=1000)
        try:
            for _ in range(3):
                with pytest.raises(PayloadTooLargeError):
                    await transport.raw(RequestSpec("GET", "/files/download", params={"path": "big"}))
                assert (
                    await transport.raw(RequestSpec("GET", "/files/download", params={"path": "small"}))
                    == b"b" * 10
                )
        finally:
            await transport.close()
    assert len(calls) == 6


def test_the_sync_client_caps_buffered_bodies_too() -> None:
    with (
        _server({"/files/download": _declared_then_slow(10_000)}) as base,
        Daytona(DaytonaConfig(api_key="k", max_response_bytes=1000)) as root,
    ):
        sandbox = lazy_sandbox(root.v2, "sbx", toolbox_url=base, access_token="t")
        started = time.monotonic()
        with pytest.raises(PayloadTooLargeError):
            sandbox.fs.read_bytes("/a")
        assert time.monotonic() - started < 2
        with pytest.raises(PayloadTooLargeError):
            sandbox.fs.read_bytes("/a", max_response_bytes=500)
