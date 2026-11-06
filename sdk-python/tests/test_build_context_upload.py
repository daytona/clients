# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Direct build-context uploads over real HTTP, async and generated sync."""

import hashlib
import io
import json
import logging
import tarfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import anyio
import httpx
import pytest

from daytona.v2 import _context_upload
from daytona.v2._bridge import SyncBridge
from daytona.v2._sync.services import ImageService
from daytona.v2._transport import AsyncTransport
from daytona.v2.errors import (
    BadRequestError,
    ConflictError,
    ConnectionError,
    DaytonaError,
    InternalServerError,
    ServiceUnavailableError,
)
from daytona.v2.services import AsyncImageService
from daytona.v2.types import Image

UPLOAD_ID = "0b6d2c8e-8a1f-4c1e-9d7a-3f0e5b2a9c41"
SIGNATURE = "X-Amz-Signature=fixture-signature"


def archive(size: int = 0) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, data in (("Dockerfile", b"FROM alpine\n"), ("blob", b"x" * size)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def part_number(path: str) -> int:
    return int(path.split("?")[0].rsplit("/", 1)[-1])


@dataclass
class Peer:
    url: str
    part_size: int = 4096
    ready: bool = False
    put_statuses: list[int] = field(default_factory=list)
    # Per part number, answered before ``put_statuses``.
    part_statuses: dict[int, list[int]] = field(default_factory=dict)
    # Runs in the server thread for each PUT, with the part number.
    on_put: Callable[[int], None] | None = None
    complete_statuses: list[int] = field(default_factory=list)
    complete_delays: list[float] = field(default_factory=list)
    # Rewrites the parts a declaration is answered with.
    rewrite_parts: Callable[[list[dict[str, object]]], list[dict[str, object]]] | None = None
    requests: list[tuple[str, str, dict[str, str], bytes]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def bodies(self, method: str, prefix: str) -> list[bytes]:
        return [body for m, path, _, body in self.requests if m == method and path.startswith(prefix)]

    def json(self, method: str, path: str) -> dict[str, object]:
        [body] = [body for m, p, _, body in self.requests if m == method and p == path]
        return dict(json.loads(body))


@contextmanager
def storage_peer(**options: object) -> Iterator[Peer]:
    holder: list[Peer] = []

    class Handler(BaseHTTPRequestHandler):
        def handle_any(self) -> None:
            peer = holder[0]
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            peer.requests.append((self.command, self.path, dict(self.headers.items()), body))
            if self.command == "PUT":
                number = part_number(self.path)
                if peer.on_put is not None:
                    peer.on_put(number)
                with peer.lock:
                    statuses = peer.part_statuses.get(number) or peer.put_statuses
                    status = statuses.pop(0) if statuses else 200
                return self.reply(status, b"")
            if self.path == "/build-contexts":
                declared = json.loads(body)
                if peer.ready:
                    return self.reply(200, self.upload("ready", declared, []))
                total, parts, offset, number = declared["bytes"], [], 0, 1
                while offset < total:
                    size = min(peer.part_size, total - offset)
                    url = f"{peer.url}/bucket/part/{number}?{SIGNATURE}"
                    parts.append({"part_number": number, "bytes": size, "url": url})
                    offset, number = offset + size, number + 1
                if peer.rewrite_parts is not None:
                    parts = peer.rewrite_parts(parts)
                return self.reply(201, self.upload("uploading", declared, parts))
            if self.path == f"/build-contexts/{UPLOAD_ID}/complete":
                with peer.lock:
                    delay = peer.complete_delays.pop(0) if peer.complete_delays else 0.0
                    status = peer.complete_statuses.pop(0) if peer.complete_statuses else 200
                time.sleep(delay)
                if status != 200:
                    return self.reply(status, {"message": "not now", "code": "UNAVAILABLE", "source": "API"})
                return self.reply(
                    200, self.upload("ready", {"dockerfile": "Dockerfile", "bytes": 1, "sha256": ""}, [])
                )
            if self.path == "/images":
                return self.reply(202, {"id": "img-ctx", "status": "pending"})
            if self.path == "/images/img-ctx":
                return self.reply(
                    200,
                    {"id": "img-ctx", "status": "pending", "region_id": "region-c", "origin": "dockerfile"},
                )
            self.reply(404, {"message": "not found", "code": "NOT_FOUND", "source": "API"})

        def upload(self, state: str, declared: dict[str, object], parts: list[object]) -> dict[str, object]:
            return {
                "id": UPLOAD_ID,
                "state": state,
                "region_id": "region-c",
                "dockerfile": declared["dockerfile"],
                "bytes": declared["bytes"],
                "sha256": declared["sha256"],
                "parts": parts,
            }

        do_GET = do_POST = do_PUT = handle_any

        def reply(self, status: int, payload: object) -> None:
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except OSError:
                # The client gave up on this request (a timeout under test).
                pass

        def log_message(self, format: str, *args: str) -> None:
            return

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        holder.append(Peer(f"http://127.0.0.1:{server.server_port}", **options))  # type: ignore[arg-type]
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            yield holder[0]
        finally:
            server.shutdown()
            thread.join()


@pytest.fixture(params=[False, True], ids=["async", "generated-sync"])
def sync(request: pytest.FixtureRequest) -> bool:
    return bool(request.param)


async def build(
    peer: Peer, context: object, sync: bool, request_timeout: float | None = None, **options: object
) -> Image:
    if sync:

        def run() -> Image:
            bridge = SyncBridge()

            async def make_transport() -> AsyncTransport:
                return AsyncTransport(peer.url, "offline", request_timeout=request_timeout)

            transport = bridge.call(make_transport)
            try:
                return ImageService(AsyncImageService(transport), bridge).build_from_context(
                    context, **options
                )  # type: ignore[arg-type]
            finally:
                bridge.close(transport.close)

        return await anyio.to_thread.run_sync(run)
    transport = AsyncTransport(peer.url, "offline", request_timeout=request_timeout)
    try:
        return await AsyncImageService(transport).build_from_context(context, **options)  # type: ignore[arg-type]
    finally:
        await transport.close()


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None:
        return

    monkeypatch.setattr(_context_upload, "_sleep", no_sleep)


@pytest.mark.anyio
@pytest.mark.parametrize("as_file", [False, True], ids=["bytes", "file"])
async def test_a_context_is_uploaded_in_parts_completed_and_built(
    sync: bool, as_file: bool, tmp_path: Path
) -> None:
    # Given a context larger than one part, as bytes or as a tar file.
    data = archive(10_000)
    context: object = data
    if as_file:
        context = tmp_path / "ctx.tar"
        context.write_bytes(data)
    with storage_peer() as peer:
        # When it is built from the context.
        image = await build(peer, context, sync, dockerfile="Dockerfile", name="web", region="region-c")
        # Then the declaration names the archive, the parts carry it whole and
        # unauthenticated, the upload is completed and the build names it.
        declared = peer.json("POST", "/build-contexts")
        assert declared == {
            "dockerfile": "Dockerfile",
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "region_id": "region-c",
        }
        puts = [r for r in peer.requests if r[0] == "PUT"]
        assert len(puts) == -(-len(data) // peer.part_size)
        assert b"".join(body for _, _, _, body in sorted(puts, key=lambda put: part_number(put[1]))) == data
        assert all("Authorization" not in headers for _, _, headers, _ in puts)
        assert peer.json("POST", f"/build-contexts/{UPLOAD_ID}/complete") == {"region_id": "region-c"}
        assert peer.json("POST", "/images") == {
            "context_upload_id": UPLOAD_ID,
            "region_id": "region-c",
            "name": "web",
        }
        assert image.id == "img-ctx"


@pytest.mark.anyio
@pytest.mark.parametrize("as_file", [False, True], ids=["bytes", "file"])
async def test_a_part_is_streamed_in_bounded_chunks_with_a_fixed_length(
    monkeypatch: pytest.MonkeyPatch, as_file: bool, tmp_path: Path
) -> None:
    # Given parts several chunks long.
    monkeypatch.setattr(_context_upload, "_PART_CHUNK", 1000)
    data = archive(10_000)
    context: object = data
    if as_file:
        context = tmp_path / "ctx.tar"
        context.write_bytes(data)
    source = _context_upload._Source(context)  # type: ignore[arg-type]
    # When a part is read, no chunk is larger than the chunk size and the
    # chunks are the part's bytes.
    chunks = [chunk async for chunk in source.chunks(4096, 4096)]
    assert max(len(chunk) for chunk in chunks) <= 1000
    assert b"".join(chunks) == data[4096:8192]
    # And on the wire each part carries its own length and is not chunked.
    with storage_peer() as peer:
        await build(peer, context, False)
        puts = [(headers, body) for m, _, headers, body in peer.requests if m == "PUT"]
        assert puts and all(headers.get("Content-Length") == str(len(body)) for headers, body in puts)
        assert all("Transfer-Encoding" not in headers for headers, _ in puts)


@pytest.mark.anyio
async def test_a_file_that_shrinks_during_the_upload_is_refused(tmp_path: Path) -> None:
    # Given a context file truncated after it was declared.
    path = tmp_path / "ctx.tar"
    path.write_bytes(archive(10_000))
    source = _context_upload._Source(path)
    path.write_bytes(b"short")
    with storage_peer() as peer:
        part = _context_upload._Part(1, 4096, f"{peer.url}/bucket/part/1?{SIGNATURE}")
        # When its part is uploaded, it fails as a changed context.
        async with httpx.AsyncClient(timeout=5) as client:
            with pytest.raises(BadRequestError, match="changed while it was being uploaded") as error:
                await _context_upload._put_part(client, source, 0, part)
        assert SIGNATURE not in str(error.value)


@pytest.mark.anyio
async def test_an_already_uploaded_context_is_built_without_uploading(sync: bool) -> None:
    # Given an API that already holds a verified upload of the same archive.
    with storage_peer(ready=True) as peer:
        # When it is built from the context.
        await build(peer, archive(), sync)
        # Then nothing is uploaded or completed, and the build names that upload.
        assert [m for m, *_ in peer.requests].count("PUT") == 0
        assert not [p for _, p, *_ in peer.requests if p.endswith("/complete")]
        assert peer.json("POST", "/images")["context_upload_id"] == UPLOAD_ID


@pytest.mark.anyio
async def test_a_part_is_retried_after_a_storage_error(sync: bool) -> None:
    # Given storage that fails the first attempt of a part.
    data = archive()
    with storage_peer(put_statuses=[503], part_size=1 << 20) as peer:
        # When it is built from the context.
        await build(peer, data, sync)
        # Then the part is sent again and the upload completes.
        assert peer.bodies("PUT", "/bucket/part/1") == [data, data]
        assert [p for _, p, *_ in peer.requests if p.endswith("/complete")]


@pytest.mark.anyio
async def test_a_refused_part_fails_without_revealing_its_url(sync: bool) -> None:
    # Given storage that refuses a part, as it does once its URL expired.
    with storage_peer(put_statuses=[403], part_size=1 << 20) as peer:
        # When it is built from the context.
        with pytest.raises(BadRequestError) as error:
            await build(peer, archive(), sync)
        # Then the error names the part but neither the URL nor its signature,
        # and nothing is completed or built.
        assert "part 1" in str(error.value)
        for text in (str(error.value), repr(error.value), repr(error.value.__cause__)):
            assert SIGNATURE not in text and "/bucket/" not in text
        assert error.value.__cause__ is None
        assert not [p for _, p, *_ in peer.requests if p.endswith("/complete") or p == "/images"]


def _swapped(parts: list[dict[str, object]]) -> list[dict[str, object]]:
    return [parts[1], parts[0]]


def _empty_part_added(parts: list[dict[str, object]]) -> list[dict[str, object]]:
    return [*parts, {**parts[-1], "part_number": len(parts) + 1, "bytes": 0}]


@pytest.mark.anyio
@pytest.mark.parametrize("rewrite", [_swapped, _empty_part_added])
async def test_a_malformed_part_layout_is_refused_before_any_upload(
    sync: bool, rewrite: Callable[[list[dict[str, object]]], list[dict[str, object]]]
) -> None:
    # Given a declaration answered with parts out of order, or with an empty part.
    with storage_peer(part_size=4096, rewrite_parts=rewrite) as peer:
        # When it is built from the context.
        with pytest.raises(DaytonaError, match="do not cover the archive"):
            await build(peer, archive(10_000), sync)
        # Then nothing is uploaded, completed or built.
        assert not [
            p for m, p, *_ in peer.requests if m == "PUT" or p.endswith("/complete") or p == "/images"
        ]


@pytest.mark.anyio
async def test_storage_that_keeps_failing_is_unavailable(sync: bool) -> None:
    with storage_peer(put_statuses=[500, 502, 503], part_size=1 << 20) as peer:
        with pytest.raises(ServiceUnavailableError) as error:
            await build(peer, archive(), sync)
        assert SIGNATURE not in str(error.value)
        assert len(peer.bodies("PUT", "/bucket/part/1")) == 3


@pytest.mark.anyio
async def test_unreachable_storage_is_a_connection_error_without_its_url() -> None:
    # Given a part whose URL points at a port nothing listens on.
    part = _context_upload._Part(1, 3, f"http://127.0.0.1:9/bucket/part/1?{SIGNATURE}")
    assert SIGNATURE not in repr(part)
    # When it is uploaded.
    async with httpx.AsyncClient(timeout=1) as client:
        with pytest.raises(ConnectionError) as error:
            await _context_upload._put_part(client, _context_upload._Source(b"abc"), 0, part)
    # Then the error names neither the URL nor the transport exception.
    assert SIGNATURE not in str(error.value) and error.value.__cause__ is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("context", "message"),
    [(b"", "build context is empty"), ("/nonexistent/ctx.tar", "is not a file")],
)
async def test_an_unusable_context_is_refused_before_any_request(context: object, message: str) -> None:
    with storage_peer() as peer:
        with pytest.raises(BadRequestError, match=message):
            await build(peer, context, False)
        assert peer.requests == []


@pytest.mark.anyio
async def test_a_context_over_one_gib_is_refused_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_context_upload, "MAX_CONTEXT_BYTES", 10)
    with storage_peer() as peer:
        with pytest.raises(BadRequestError, match="exceeds the 1 GiB limit"):
            await build(peer, archive(), False)
        assert peer.requests == []


def completes(peer: Peer) -> int:
    return sum(1 for _, p, *_ in peer.requests if p.endswith("/complete"))


@pytest.mark.anyio
async def test_no_log_record_names_a_part_url(sync: bool, caplog: pytest.LogCaptureFixture) -> None:
    # Given every logger at DEBUG, as an application that turns logging up has it.
    caplog.set_level(logging.DEBUG)
    with storage_peer() as peer:
        # When a context of several parts is uploaded.
        await build(peer, archive(10_000), sync)
    # Then httpx logged the part requests, but no record names a part URL or
    # its signature, and the redaction is released once the upload is done.
    messages = [record.getMessage() for record in caplog.records]
    assert any("HTTP Request: PUT <build context part URL>" in message for message in messages)
    assert any("/build-contexts" in message for message in messages)
    for message in messages:
        assert SIGNATURE not in message and "/bucket/" not in message
    assert _context_upload._redact_part_urls._urls == {}


def test_the_redaction_leaves_other_records_alone() -> None:
    record = logging.LogRecord("httpx", logging.INFO, __file__, 1, "HTTP Request: %s %s", ("PUT", "x"), None)
    url = f"http://127.0.0.1:1/bucket/part/1?{SIGNATURE}"
    with _context_upload._redact_part_urls.active([url]):
        assert _context_upload._redact_part_urls.filter(record)
        assert record.getMessage() == "HTTP Request: PUT x"
        record.args = ("PUT", httpx.URL(url))
        _context_upload._redact_part_urls.filter(record)
        assert record.getMessage() == "HTTP Request: PUT <build context part URL>"
    record.args = ("PUT", url)
    _context_upload._redact_part_urls.filter(record)
    assert record.getMessage() == f"HTTP Request: PUT {url}"


@pytest.mark.anyio
@pytest.mark.parametrize("as_file", [False, True], ids=["bytes", "file"])
async def test_parts_are_uploaded_in_parallel_up_to_the_limit(
    sync: bool, as_file: bool, tmp_path: Path
) -> None:
    # Given storage that holds each PUT until the limit is in flight.
    limit = _context_upload._PART_CONCURRENCY
    lock, full = threading.Lock(), threading.Event()
    in_flight, peak = [0], [0]

    def hold(_: int) -> None:
        with lock:
            in_flight[0] += 1
            peak[0] = max(peak[0], in_flight[0])
            if in_flight[0] == limit:
                full.set()
        full.wait(5)
        with lock:
            in_flight[0] -= 1

    data = archive(10_000)
    context: object = data
    if as_file:
        context = tmp_path / "ctx.tar"
        context.write_bytes(data)
    with storage_peer(part_size=1024, on_put=hold) as peer:
        # When a context of more parts than the limit is uploaded.
        await build(peer, context, sync)
        # Then exactly the limit was in flight at once, every part carries its
        # own slice of the archive, and the upload is completed once.
        assert peak[0] == limit
        puts = [(path, body) for m, path, _, body in peer.requests if m == "PUT"]
        assert len(puts) == -(-len(data) // 1024) > limit
        for path, body in puts:
            number = part_number(path)
            assert body == data[(number - 1) * 1024 : number * 1024]
        assert completes(peer) == 1


@pytest.mark.anyio
async def test_a_refused_part_among_parallel_parts_is_the_error_raised(sync: bool) -> None:
    # Given storage that refuses the second of several parts.
    with storage_peer(part_size=1024, part_statuses={2: [403]}) as peer:
        # When the context is uploaded, that refusal is raised as itself.
        with pytest.raises(BadRequestError, match="part 2 ") as error:
            await build(peer, archive(10_000), sync)
        assert SIGNATURE not in str(error.value)
        assert completes(peer) == 0


@pytest.mark.anyio
async def test_a_complete_the_server_failed_is_retried(sync: bool) -> None:
    with storage_peer(complete_statuses=[503, 502]) as peer:
        image = await build(peer, archive(), sync)
        assert completes(peer) == 3
        assert image.id == "img-ctx"


@pytest.mark.anyio
async def test_a_complete_that_keeps_failing_gives_up(sync: bool) -> None:
    with storage_peer(complete_statuses=[500, 500, 500]) as peer:
        with pytest.raises(InternalServerError) as error:
            await build(peer, archive(), sync)
        assert error.value.status_code == 500
        assert completes(peer) == _context_upload._COMPLETE_ATTEMPTS
        assert not [p for _, p, *_ in peer.requests if p == "/images"]


@pytest.mark.anyio
async def test_a_complete_the_server_refused_is_not_retried(sync: bool) -> None:
    with storage_peer(complete_statuses=[409]) as peer:
        with pytest.raises(ConflictError):
            await build(peer, archive(), sync)
        assert completes(peer) == 1


@pytest.mark.anyio
async def test_a_complete_outlives_the_request_timeout(sync: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a short request timeout and an API that takes longer to verify.
    monkeypatch.setattr(_context_upload, "_COMPLETE_TIMEOUT_SECONDS", 5.0)
    with storage_peer(complete_delays=[0.5]) as peer:
        # When the context is built, the one complete call is waited for.
        await build(peer, archive(), sync, request_timeout=0.2)
        assert completes(peer) == 1


@pytest.mark.anyio
async def test_a_complete_that_timed_out_is_retried(sync: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given an API whose first complete outlasts the complete timeout.
    monkeypatch.setattr(_context_upload, "_COMPLETE_TIMEOUT_SECONDS", 0.3)
    with storage_peer(complete_delays=[1.0]) as peer:
        # When the context is built, the complete is sent again and succeeds.
        image = await build(peer, archive(), sync, request_timeout=0.1)
        assert completes(peer) == 2
        assert image.id == "img-ctx"


@pytest.mark.anyio
async def test_a_buffer_context_is_read_without_copying_it() -> None:
    # Given a writable buffer and a view of it.
    data = bytearray(archive(10_000))
    for context in (data, memoryview(data)):
        source = _context_upload._Source(context)
        # When the archive is read, it is read from the caller's buffer.
        assert source._data is not None and source._data.obj is data
        assert source.sha256() == hashlib.sha256(data).hexdigest()
        assert b"".join([chunk async for chunk in source.chunks(0, source.size)]) == data


@pytest.mark.anyio
async def test_a_non_contiguous_view_is_still_accepted() -> None:
    data = archive(1_000)
    view = memoryview(data)[::2]
    source = _context_upload._Source(view)
    assert source.size == len(data[::2])
    assert b"".join([chunk async for chunk in source.chunks(0, source.size)]) == data[::2]


@pytest.mark.anyio
async def test_a_name_with_an_idle_expiry_is_refused_before_any_request(sync: bool) -> None:
    with storage_peer() as peer:
        with pytest.raises(BadRequestError, match="applies only to an anonymous image"):
            await build(peer, archive(), sync, name="web", idle_expiry_seconds=60)
        assert peer.requests == []


@pytest.mark.anyio
@pytest.mark.parametrize(("timeout", "expected"), [(None, None), (0, None), (30.0, 30.0)])
async def test_the_build_request_takes_the_timeout(
    monkeypatch: pytest.MonkeyPatch, timeout: float | None, expected: float | None
) -> None:
    sent: list[object] = []
    original = AsyncTransport.send

    async def record(self: AsyncTransport, spec: object) -> object:
        sent.append(spec)
        return await original(self, spec)  # type: ignore[arg-type]

    monkeypatch.setattr(AsyncTransport, "send", record)
    with storage_peer() as peer:
        await build(peer, archive(), False, timeout=timeout)
    [spec] = [spec for spec in sent if getattr(spec, "path", None) == "/images" and spec.method == "POST"]  # type: ignore[attr-defined]
    assert spec.timeout == expected  # type: ignore[attr-defined]
    assert spec.unbounded_read is (timeout in (None, 0))  # type: ignore[attr-defined]
