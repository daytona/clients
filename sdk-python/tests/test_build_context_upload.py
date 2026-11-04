# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Direct build-context uploads over real HTTP, async and generated sync."""

import hashlib
import io
import json
import tarfile
from collections.abc import Iterator
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
from daytona.v2.errors import BadRequestError, ConnectionError, ServiceUnavailableError
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


@dataclass
class Peer:
    url: str
    part_size: int = 4096
    ready: bool = False
    put_statuses: list[int] = field(default_factory=list)
    requests: list[tuple[str, str, dict[str, str], bytes]] = field(default_factory=list)

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
                status = peer.put_statuses.pop(0) if peer.put_statuses else 200
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
                return self.reply(201, self.upload("uploading", declared, parts))
            if self.path == f"/build-contexts/{UPLOAD_ID}/complete":
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
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

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


async def build(peer: Peer, context: object, sync: bool, **options: object) -> Image:
    if sync:

        def run() -> Image:
            bridge = SyncBridge()

            async def make_transport() -> AsyncTransport:
                return AsyncTransport(peer.url, "offline")

            transport = bridge.call(make_transport)
            try:
                return ImageService(AsyncImageService(transport), bridge).build_from_context(
                    context, **options
                )  # type: ignore[arg-type]
            finally:
                bridge.close(transport.close)

        return await anyio.to_thread.run_sync(run)
    transport = AsyncTransport(peer.url, "offline")
    try:
        return await AsyncImageService(transport).build_from_context(context, **options)  # type: ignore[arg-type]
    finally:
        await transport.close()


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None:
        return

    monkeypatch.setattr(_context_upload.anyio, "sleep", no_sleep)


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
        assert b"".join(peer.bodies("PUT", "/bucket/part/")) == data
        assert all("Authorization" not in headers for _, _, headers, _ in puts)
        assert peer.json("POST", f"/build-contexts/{UPLOAD_ID}/complete") == {"region_id": "region-c"}
        assert peer.json("POST", "/images") == {
            "context_upload_id": UPLOAD_ID,
            "region_id": "region-c",
            "name": "web",
        }
        assert image.id == "img-ctx"


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
