# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import io
import os
import stat
from pathlib import Path

import httpx
import pytest

import daytona.v2.errors as v2_errors
from daytona.v2 import AtomicUploadUnsupportedError, DaytonaError, TransferCancelledError
from daytona.v2._core import RequestSpec
from daytona.v2._transport import AsyncTransport, _decode_response
from daytona.v2.filesystem import (
    AsyncFileSystemService,
    _await_cancellable,
    _multipart_preamble,
    _read_download_chunk,
)
from daytona.v2.git import AsyncGitService
from daytona.v2.sandbox import AsyncSandbox, sandbox_info_from_id


class SendTransport:
    def __init__(self, responses: list[dict[str, object] | None]) -> None:
        self._responses = list(responses)
        self.specs: list[RequestSpec] = []

    async def send(self, spec: RequestSpec):
        self.specs.append(spec)
        if not self._responses:
            return None
        return self._responses.pop(0)


class UploadTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.chunks: list[bytes] = []

    async def request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        self.calls.append((method, path, dict(kwargs)))
        content = kwargs.get("content")
        if content is None:
            content_factory = kwargs.get("content_factory")
            assert callable(content_factory)
            content = content_factory(0)
        assert hasattr(content, "__aiter__")
        async for chunk in content.__aiter__():
            assert isinstance(chunk, bytes)
            self.chunks.append(chunk)
        return httpx.Response(204)


class UploadRetryTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.bodies: list[bytes] = []
        self.authorizations: list[str] = []
        self.upload_calls = 0
        self.closed_401_responses = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/files/upload":
            raise AssertionError(request.url.path)
        self.upload_calls += 1
        self.authorizations.append(request.headers["authorization"])
        self.bodies.append(await request.aread())
        if self.upload_calls == 1:
            response = httpx.Response(
                401,
                headers={"content-type": "application/json"},
                json={"message": "expired", "code": "UNAUTHORIZED", "source": "DAYTONA_API"},
            )
            original_aclose = response.aclose

            async def tracked_aclose() -> None:
                self.closed_401_responses += 1
                await original_aclose()

            response.aclose = tracked_aclose  # type: ignore[method-assign]
            return response
        return httpx.Response(204)


class RefreshingAuth:
    def __init__(self) -> None:
        self.calls: list[tuple[bool, str | None]] = []

    async def get_token(self, *, force_refresh: bool = False, current_token: str | None = None) -> str:
        self.calls.append((force_refresh, current_token))
        return "platform-2" if force_refresh else "platform-1"


class DownloadStream:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        headers: dict[str, str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._chunks = list(chunks)
        self.headers = headers or {}
        self._error = error
        self.closed = False

    async def iter_bytes(self):
        for chunk in self._chunks:
            yield chunk
        if self._error is not None:
            raise self._error

    async def close(self) -> None:
        self.closed = True


class DownloadTransport:
    def __init__(self, stream: DownloadStream) -> None:
        self._stream = stream
        self.specs: list[RequestSpec] = []

    async def open_stream(self, spec: RequestSpec) -> DownloadStream:
        self.specs.append(spec)
        return self._stream


class PartialRetryUploadTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.authorizations: list[str] = []
        self.upload_calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/files/upload":
            raise AssertionError(request.url.path)
        self.upload_calls += 1
        self.authorizations.append(request.headers["authorization"])
        if self.upload_calls == 1:
            stream_iter = request.stream.__aiter__()
            await anext(stream_iter)
            await anext(stream_iter)
            return httpx.Response(
                401,
                headers={"content-type": "application/json"},
                json={"message": "expired", "code": "UNAUTHORIZED", "source": "DAYTONA_API"},
            )
        await request.aread()
        return httpx.Response(204)


class NeverYieldingDownloadStream:
    def __init__(self) -> None:
        self.headers = {"Content-Length": "6"}
        self.closed = False
        self.started = asyncio.Event()
        self._release = asyncio.Event()

    async def iter_bytes(self):
        self.started.set()
        await self._release.wait()
        yield b"never"

    async def close(self) -> None:
        self.closed = True
        self._release.set()


class NeverOpeningDownloadTransport:
    def __init__(self) -> None:
        self.specs: list[RequestSpec] = []
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def open_stream(self, spec: RequestSpec) -> DownloadStream:
        self.specs.append(spec)
        self.started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError("unreachable")


@pytest.mark.anyio
async def test_git_clone_push_and_pull_include_optional_fields_only_when_set() -> None:
    transport = SendTransport([None, None, None, None])
    git = AsyncGitService(transport)

    await git.clone("https://example.invalid/repo.git", "/repo")
    await git.clone(
        "https://example.invalid/repo.git",
        "/repo",
        depth=2,
        insecure_skip_tls=True,
        branch="main",
    )
    await git.pull("/repo", branch="release", remote="origin")
    await git.push("/repo", branch="release", remote="origin", set_upstream=True)

    assert transport.specs[0].json_body == {"url": "https://example.invalid/repo.git", "path": "/repo"}
    assert transport.specs[1].json_body == {
        "url": "https://example.invalid/repo.git",
        "path": "/repo",
        "branch": "main",
        "depth": 2,
        "insecureSkipTls": True,
    }
    assert transport.specs[2].json_body == {"path": "/repo", "branch": "release", "remote": "origin"}
    assert transport.specs[3].json_body == {
        "path": "/repo",
        "branch": "release",
        "remote": "origin",
        "setUpstream": True,
    }


@pytest.mark.anyio
async def test_git_clone_rejects_non_boolean_insecure_skip_tls_before_request() -> None:
    transport = SendTransport([])
    git = AsyncGitService(transport)

    with pytest.raises(DaytonaError, match="insecure_skip_tls must be a boolean") as exc_info:
        await git.clone("https://example.invalid/repo.git", "/repo", insecure_skip_tls="false")  # type: ignore[arg-type]

    assert exc_info.value.code == "BAD_REQUEST"
    assert transport.specs == []


@pytest.mark.anyio
@pytest.mark.parametrize("depth", [0, 1.5, True])
async def test_git_clone_rejects_invalid_depth_before_request(depth: object) -> None:
    transport = SendTransport([])
    git = AsyncGitService(transport)

    with pytest.raises(Exception, match="depth must be a positive integer"):
        await git.clone("https://example.invalid/repo.git", "/repo", depth=depth)

    assert transport.specs == []


@pytest.mark.anyio
async def test_sandbox_update_env_posts_validated_body() -> None:
    toolbox = SendTransport([None, None])
    sandbox = AsyncSandbox(sandbox_info_from_id("sbx"), None, toolbox)

    await sandbox.update_env({"A": "1", "B": "2"}, unset=("OLD",))
    await sandbox.update_env(unset=("TMP",))

    assert transport_payloads(toolbox.specs) == [
        ("POST", "/env", {"env": {"A": "1", "B": "2"}, "unset": ["OLD"]}),
        ("POST", "/env", {"env": {}, "unset": ["TMP"]}),
    ]


@pytest.mark.anyio
async def test_sandbox_update_env_forwards_names_unchanged() -> None:
    toolbox = SendTransport([None])
    sandbox = AsyncSandbox(sandbox_info_from_id("sbx"), None, toolbox)

    await sandbox.update_env({"APP_DEBUG": "1"})

    assert transport_payloads(toolbox.specs) == [("POST", "/env", {"env": {"APP_DEBUG": "1"}, "unset": []})]


@pytest.mark.anyio
async def test_sandbox_update_env_rejects_invalid_keys_before_request() -> None:
    toolbox = SendTransport([])
    sandbox = AsyncSandbox(sandbox_info_from_id("sbx"), None, toolbox)

    with pytest.raises(Exception, match="env key is not a valid variable name"):
        await sandbox.update_env({"": "1"})
    with pytest.raises(Exception, match="env key is not a valid variable name"):
        await sandbox.update_env({"BAD\0KEY": "1"})
    with pytest.raises(Exception, match="must not contain NUL"):
        await sandbox.update_env({"OK": "BAD\0VALUE"})
    with pytest.raises(Exception, match="env key is not a valid variable name"):
        await sandbox.update_env(unset=("BAD=KEY",))
    with pytest.raises(Exception, match="env key is not a valid variable name"):
        await sandbox.update_env({"BAD\nKEY": "1"})
    with pytest.raises(Exception, match="env key is not a valid variable name"):
        await sandbox.update_env({"BAD\rKEY": "1"})

    assert toolbox.specs == []


@pytest.mark.anyio
async def test_sandbox_update_env_allows_noncredential_daytona_keys() -> None:
    toolbox = SendTransport([None])
    sandbox = AsyncSandbox(sandbox_info_from_id("sbx"), None, toolbox)

    await sandbox.update_env({"DAYTONA_API_URL": "https://api.example.com", "APP_PORT": "8080"})

    assert transport_payloads(toolbox.specs) == [
        (
            "POST",
            "/env",
            {"env": {"DAYTONA_API_URL": "https://api.example.com", "APP_PORT": "8080"}, "unset": []},
        )
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"unset": "FOO"}, "env unset must be a non-string sequence of strings"),
        ({"unset": b"FOO"}, "env unset must be a non-string sequence of strings"),
        ({"env": ["A"]}, "env must be a mapping of string keys to string values"),
    ],
)
async def test_sandbox_update_env_rejects_invalid_env_container_shapes_before_request(
    kwargs: dict[str, object], message: str
) -> None:
    toolbox = SendTransport([])
    sandbox = AsyncSandbox(sandbox_info_from_id("sbx"), None, toolbox)

    with pytest.raises(Exception, match=message):
        await sandbox.update_env(**kwargs)

    assert toolbox.specs == []


@pytest.mark.anyio
async def test_upload_file_streams_progress_and_atomic_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("daytona.v2.filesystem._FILE_TRANSFER_CHUNK_BYTES", 3)
    local_path = tmp_path / "payload.bin"
    local_path.write_bytes(b"abcdefghi")
    transport = UploadTransport()
    fs = AsyncFileSystemService(transport)
    progress: list[tuple[int, int | None]] = []

    await fs.upload_file(
        local_path,
        "/remote/payload.bin",
        on_progress=lambda sent, total: progress.append((sent, total)),
        atomic=True,
    )

    assert progress == [(3, 9), (6, 9), (9, 9)]
    assert [(method, path) for method, path, _ in transport.calls] == [("POST", "/files/upload")]
    call_kwargs = transport.calls[0][2]
    assert call_kwargs["params"] == {"path": "/remote/payload.bin", "atomic": True}
    assert "content" not in call_kwargs
    body = b"".join(transport.chunks)
    boundary = call_kwargs["headers"]["Content-Type"].split("boundary=", 1)[1]
    assert body.startswith(f"--{boundary}\r\n".encode())
    assert b'filename="payload.bin"' in body
    assert b"abcdefghi" in body
    assert body.endswith(f"\r\n--{boundary}--\r\n".encode())


class StalledAfterBodyTransport(UploadTransport):
    """Drains the request body without returning a response."""

    def __init__(self) -> None:
        super().__init__()
        self.drained = asyncio.Event()
        self.cancelled = False

    async def request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        self.calls.append((method, path, dict(kwargs)))
        content_factory = kwargs.get("content_factory")
        assert callable(content_factory)
        async for chunk in content_factory(0).__aiter__():
            self.chunks.append(chunk)
        self.drained.set()
        try:
            await asyncio.Event().wait()  # never set: the response never arrives
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("unreachable")


@pytest.mark.anyio
async def test_upload_file_stays_cancellable_after_the_body_is_exhausted(tmp_path: Path) -> None:
    local_path = tmp_path / "payload.bin"
    local_path.write_bytes(b"abc")
    transport = StalledAfterBodyTransport()
    fs = AsyncFileSystemService(transport)
    cancel = asyncio.Event()

    async def cancel_once_drained() -> None:
        await transport.drained.wait()
        cancel.set()

    canceller = asyncio.create_task(cancel_once_drained())
    with pytest.raises(TransferCancelledError, match="upload transfer cancelled") as raised:
        await asyncio.wait_for(fs.upload_file(local_path, "/remote.bin", cancel=cancel), timeout=5)
    await canceller
    assert transport.cancelled, "the in-flight request must be torn down when cancellation wins"
    assert (raised.value.status_code, raised.value.code, raised.value.source) == (
        499,
        "REQUEST_CANCELLED",
        "SDK",
    )


@pytest.mark.anyio
async def test_empty_upload_reports_progress_before_the_closing_boundary(tmp_path: Path) -> None:
    # The final on_progress(0, 0) runs before the closing boundary is yielded, so a
    # callback that cancels still stops the request.
    local_path = tmp_path / "empty.bin"
    local_path.write_bytes(b"")
    transport = UploadTransport()
    fs = AsyncFileSystemService(transport)
    cancel = asyncio.Event()
    seen: list[tuple[int, int | None]] = []

    def on_progress(sent: int, total: int | None) -> None:
        seen.append((sent, total))
        cancel.set()

    with pytest.raises(TransferCancelledError, match="upload transfer cancelled"):
        await fs.upload_file(local_path, "/remote.bin", on_progress=on_progress, cancel=cancel)
    assert seen == [(0, 0)]
    # Only the preamble went out; the `--<boundary>--` terminator was never yielded.
    body = b"".join(transport.chunks)
    assert body.startswith(b"--") and b'name="file"' in body
    assert not body.rstrip(b"\r\n").endswith(b"--"), "closing boundary must not be sent"


@pytest.mark.anyio
async def test_upload_file_checks_cancellation_before_touching_the_local_path(tmp_path: Path) -> None:
    # A pre-cancelled upload raises TransferCancelledError even when the local
    # path is missing: cancellation is checked before stat().
    transport = UploadTransport()
    fs = AsyncFileSystemService(transport)
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(TransferCancelledError, match="upload transfer cancelled"):
        await fs.upload_file(tmp_path / "does-not-exist.bin", "/remote.bin", cancel=cancel)
    assert transport.calls == []


@pytest.mark.anyio
async def test_upload_file_cancel_mid_stream_raises_typed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("daytona.v2.filesystem._FILE_TRANSFER_CHUNK_BYTES", 3)
    local_path = tmp_path / "payload.bin"
    local_path.write_bytes(b"abcdefghi")
    transport = UploadTransport()
    fs = AsyncFileSystemService(transport)
    cancel = asyncio.Event()
    progress: list[tuple[int, int | None]] = []

    def on_progress(sent: int, total: int | None) -> None:
        progress.append((sent, total))
        cancel.set()

    with pytest.raises(TransferCancelledError, match="upload transfer cancelled"):
        await fs.upload_file(local_path, "/remote/payload.bin", on_progress=on_progress, cancel=cancel)

    assert progress == [(3, 9)]
    assert transport.calls[0][2]["params"] == {"path": "/remote/payload.bin"}
    boundary = transport.calls[0][2]["headers"]["Content-Type"].split("boundary=", 1)[1]
    assert b"".join(transport.chunks).startswith(f"--{boundary}\r\n".encode())


@pytest.mark.anyio
async def test_upload_file_uses_unique_boundary_per_request(tmp_path: Path) -> None:
    local_path = tmp_path / "payload.bin"
    local_path.write_bytes(b"abcdef")
    transport = UploadTransport()
    fs = AsyncFileSystemService(transport)

    await fs.upload_file(local_path, "/remote/one.bin")
    first_headers = transport.calls[-1][2]["headers"]
    first_body = b"".join(transport.chunks)

    transport.chunks.clear()
    await fs.upload_file(local_path, "/remote/two.bin")
    second_headers = transport.calls[-1][2]["headers"]
    second_body = b"".join(transport.chunks)

    first_boundary = first_headers["Content-Type"].split("boundary=", 1)[1]
    second_boundary = second_headers["Content-Type"].split("boundary=", 1)[1]
    assert first_boundary != second_boundary
    assert first_body.startswith(f"--{first_boundary}\r\n".encode())
    assert second_body.startswith(f"--{second_boundary}\r\n".encode())


@pytest.mark.anyio
async def test_upload_file_retries_401_with_fresh_streamed_body(tmp_path: Path) -> None:
    local_path = tmp_path / "payload.bin"
    local_path.write_bytes(b"abcdef")
    retry_transport = UploadRetryTransport()
    auth = RefreshingAuth()
    http = httpx.AsyncClient(transport=retry_transport, base_url="http://toolbox.invalid")
    transport = AsyncTransport("http://toolbox.invalid", client=http, auth=auth)  # type: ignore[arg-type]
    fs = AsyncFileSystemService(transport)

    try:
        await fs.upload_file(local_path, "/remote/payload.bin")
    finally:
        await http.aclose()
        await transport.close()

    assert retry_transport.upload_calls == 2
    assert retry_transport.authorizations == ["Bearer platform-1", "Bearer platform-2"]
    assert retry_transport.bodies[0] == retry_transport.bodies[1]
    assert b"abcdef" in retry_transport.bodies[1]
    assert retry_transport.closed_401_responses == 1
    assert auth.calls == [(False, None), (True, "platform-1")]


@pytest.mark.anyio
async def test_upload_file_retry_progress_stays_monotonic_after_401(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("daytona.v2.filesystem._FILE_TRANSFER_CHUNK_BYTES", 3)
    local_path = tmp_path / "payload.bin"
    local_path.write_bytes(b"abcdef")
    retry_transport = PartialRetryUploadTransport()
    auth = RefreshingAuth()
    http = httpx.AsyncClient(transport=retry_transport, base_url="http://toolbox.invalid")
    transport = AsyncTransport("http://toolbox.invalid", client=http, auth=auth)  # type: ignore[arg-type]
    fs = AsyncFileSystemService(transport)
    progress: list[tuple[int, int | None]] = []

    try:
        await fs.upload_file(
            local_path,
            "/remote/payload.bin",
            on_progress=lambda sent, total: progress.append((sent, total)),
        )
    finally:
        await http.aclose()
        await transport.close()

    assert retry_transport.upload_calls == 2
    assert progress == [(3, 6), (6, 6)]


@pytest.mark.anyio
async def test_upload_file_swallows_progress_callback_errors(tmp_path: Path) -> None:
    local_path = tmp_path / "payload.bin"
    local_path.write_bytes(b"abcdef")
    transport = UploadTransport()
    fs = AsyncFileSystemService(transport)
    progress_calls: list[tuple[int, int | None]] = []

    def on_progress(sent: int, total: int | None) -> None:
        progress_calls.append((sent, total))
        raise RuntimeError("progress failed")

    await fs.upload_file(
        local_path,
        "/remote/payload.bin",
        on_progress=on_progress,
    )

    assert progress_calls == [(6, 6)]


@pytest.mark.anyio
async def test_upload_file_ignores_growth_past_the_initial_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("daytona.v2.filesystem._FILE_TRANSFER_CHUNK_BYTES", 3)
    local_path = tmp_path / "payload.bin"
    local_path.write_bytes(b"abcdef")
    transport = UploadTransport()
    fs = AsyncFileSystemService(transport)

    original_open = Path.open

    def growing_open(self: Path, *args: object, **kwargs: object):
        if self == local_path:
            return io.BytesIO(b"abcdefghi")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", growing_open)

    await fs.upload_file(local_path, "/remote/payload.bin")

    body = b"".join(transport.chunks)
    assert b"abcdef" in body
    assert b"abcdefghi" not in body


@pytest.mark.anyio
async def test_upload_file_raises_when_source_file_shrinks_mid_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("daytona.v2.filesystem._FILE_TRANSFER_CHUNK_BYTES", 3)
    local_path = tmp_path / "payload.bin"
    local_path.write_bytes(b"abcdef")
    transport = UploadTransport()
    fs = AsyncFileSystemService(transport)

    original_open = Path.open

    def shrinking_open(self: Path, *args: object, **kwargs: object):
        if self == local_path:
            return io.BytesIO(b"abc")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", shrinking_open)

    with pytest.raises(v2_errors.BadRequestError, match="source file changed size during upload") as exc_info:
        await fs.upload_file(local_path, "/remote/payload.bin")

    assert (exc_info.value.status_code, exc_info.value.code, exc_info.value.source) == (
        400,
        "BAD_REQUEST",
        "SDK",
    )


def test_upload_preamble_escapes_filename_control_characters() -> None:
    preamble = _multipart_preamble("test-boundary", 'a\\b"c\r\nd.bin')

    assert preamble.startswith(b"--test-boundary\r\n")
    assert b'filename="a\\\\b\\"c\\r\\nd.bin"' in preamble


@pytest.mark.anyio
async def test_download_file_writes_exact_content_and_reports_length(tmp_path: Path) -> None:
    destination = tmp_path / "download.bin"
    stream = DownloadStream([b"ab", b"cd", b"ef"], headers={"Content-Length": "6"})
    transport = DownloadTransport(stream)
    fs = AsyncFileSystemService(transport)
    progress: list[tuple[int, int | None]] = []

    await fs.download_file(
        "/remote.bin", destination, on_progress=lambda sent, total: progress.append((sent, total))
    )

    assert destination.read_bytes() == b"abcdef"
    assert not destination.with_name("download.bin.part").exists()
    # Ordinary creation mode under the process umask, not NamedTemporaryFile's 0600.
    mask = os.umask(0)
    os.umask(mask)
    assert stat.S_IMODE(destination.stat().st_mode) == (0o666 & ~mask)
    assert progress == [(2, 6), (4, 6), (6, 6)]
    assert transport.specs[0].path == "/files/download"
    assert transport.specs[0].params == {"path": "/remote.bin"}
    assert stream.closed is True


@pytest.mark.anyio
async def test_download_file_reports_unknown_total_when_content_length_is_missing(tmp_path: Path) -> None:
    destination = tmp_path / "download.bin"
    stream = DownloadStream([b"ab", b"cd"])
    transport = DownloadTransport(stream)
    fs = AsyncFileSystemService(transport)
    progress: list[tuple[int, int | None]] = []

    await fs.download_file(
        "/remote.bin", destination, on_progress=lambda sent, total: progress.append((sent, total))
    )

    assert destination.read_bytes() == b"abcd"
    assert progress == [(2, None), (4, None)]


@pytest.mark.anyio
async def test_download_file_cancel_removes_partial_file(tmp_path: Path) -> None:
    destination = tmp_path / "download.bin"
    stream = DownloadStream([b"ab", b"cd"])
    transport = DownloadTransport(stream)
    fs = AsyncFileSystemService(transport)
    cancel = asyncio.Event()

    def on_progress(_sent: int, _total: int | None) -> None:
        cancel.set()

    with pytest.raises(TransferCancelledError, match="download transfer cancelled"):
        await fs.download_file("/remote.bin", destination, on_progress=on_progress, cancel=cancel)

    assert not destination.exists()
    assert not destination.with_name("download.bin.part").exists()
    assert stream.closed is True


@pytest.mark.anyio
async def test_download_file_cancel_on_final_progress_tick_does_not_commit(tmp_path: Path) -> None:
    destination = tmp_path / "download.bin"
    stream = DownloadStream([b"abcd"], headers={"Content-Length": "4"})
    transport = DownloadTransport(stream)
    fs = AsyncFileSystemService(transport)
    cancel = asyncio.Event()

    def on_progress(_sent: int, _total: int | None) -> None:
        cancel.set()

    with pytest.raises(TransferCancelledError, match="download transfer cancelled"):
        await fs.download_file("/remote.bin", destination, on_progress=on_progress, cancel=cancel)

    assert not destination.exists()
    assert list(tmp_path.glob(".daytona-dl-*")) == []
    assert stream.closed is True


@pytest.mark.anyio
async def test_download_file_cancel_beats_stalled_stream_and_removes_temp(tmp_path: Path) -> None:
    destination = tmp_path / "download.bin"
    stream = NeverYieldingDownloadStream()
    transport = DownloadTransport(stream)  # type: ignore[arg-type]
    fs = AsyncFileSystemService(transport)
    cancel = asyncio.Event()
    task = asyncio.create_task(fs.download_file("/remote.bin", destination, cancel=cancel))

    await asyncio.wait_for(stream.started.wait(), timeout=1)
    cancel.set()

    with pytest.raises(TransferCancelledError, match="download transfer cancelled"):
        await asyncio.wait_for(task, timeout=1)

    assert not destination.exists()
    assert list(tmp_path.glob(".daytona-dl-*")) == []
    assert stream.closed is True


@pytest.mark.anyio
async def test_download_file_cancel_beats_stalled_header_read_and_creates_no_temp_file(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "download.bin"
    transport = NeverOpeningDownloadTransport()
    fs = AsyncFileSystemService(transport)  # type: ignore[arg-type]
    cancel = asyncio.Event()
    task = asyncio.create_task(fs.download_file("/remote.bin", destination, cancel=cancel))

    await asyncio.wait_for(transport.started.wait(), timeout=1)
    cancel.set()

    with pytest.raises(TransferCancelledError, match="download transfer cancelled"):
        await asyncio.wait_for(task, timeout=1)

    await asyncio.wait_for(transport.cancelled.wait(), timeout=1)
    assert not destination.exists()
    assert list(tmp_path.glob(".daytona-dl-*")) == []


@pytest.mark.anyio
async def test_download_file_swallows_progress_callback_errors(tmp_path: Path) -> None:
    destination = tmp_path / "download.bin"
    stream = DownloadStream([b"ab", b"cd"], headers={"Content-Length": "4"})
    transport = DownloadTransport(stream)
    fs = AsyncFileSystemService(transport)
    progress_calls: list[tuple[int, int | None]] = []

    def on_progress(sent: int, total: int | None) -> None:
        progress_calls.append((sent, total))
        raise RuntimeError("progress failed")

    await fs.download_file("/remote.bin", destination, on_progress=on_progress)

    assert destination.read_bytes() == b"abcd"
    assert progress_calls == [(2, 4), (4, 4)]
    assert stream.closed is True


@pytest.mark.anyio
async def test_await_cancellable_propagates_task_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("daytona.v2.filesystem._TRANSFER_CANCEL_POLL_SECONDS", 0.01)

    async def raise_timeout() -> str:
        await asyncio.sleep(0.02)
        raise asyncio.TimeoutError("task timed out")

    with pytest.raises(asyncio.TimeoutError, match="task timed out"):
        await asyncio.wait_for(
            _await_cancellable(raise_timeout(), cancel=asyncio.Event(), operation="download"), timeout=1
        )


@pytest.mark.anyio
async def test_read_download_chunk_propagates_iterator_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("daytona.v2.filesystem._TRANSFER_CANCEL_POLL_SECONDS", 0.01)

    async def chunks():
        await asyncio.sleep(0.02)
        raise asyncio.TimeoutError("iterator timed out")
        if False:
            yield b""

    with pytest.raises(asyncio.TimeoutError, match="iterator timed out"):
        await asyncio.wait_for(_read_download_chunk(chunks().__aiter__(), cancel=asyncio.Event()), timeout=1)


@pytest.mark.anyio
async def test_download_file_ignores_preplanted_part_symlink(tmp_path: Path) -> None:
    destination = tmp_path / "download.bin"
    old_target = tmp_path / "old-target.bin"
    old_target.write_bytes(b"untouched")
    planted_part = destination.with_name("download.bin.part")
    try:
        planted_part.symlink_to(old_target)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symlinks unavailable: {error}")
    stream = DownloadStream([b"ab", b"cd"])
    transport = DownloadTransport(stream)
    fs = AsyncFileSystemService(transport)

    await fs.download_file("/remote.bin", destination)

    assert destination.read_bytes() == b"abcd"
    assert old_target.read_bytes() == b"untouched"
    assert planted_part.is_symlink()
    assert planted_part.resolve() == old_target.resolve()


@pytest.mark.anyio
async def test_download_file_failure_removes_partial_file(tmp_path: Path) -> None:
    destination = tmp_path / "download.bin"
    stream = DownloadStream([b"ab"], error=OSError("boom"))
    transport = DownloadTransport(stream)
    fs = AsyncFileSystemService(transport)

    with pytest.raises(OSError, match="boom"):
        await fs.download_file("/remote.bin", destination)

    assert not destination.exists()
    assert not destination.with_name("download.bin.part").exists()
    assert stream.closed is True


@pytest.mark.anyio
async def test_download_file_replacing_existing_file_preserves_destination_mode(tmp_path: Path) -> None:
    if not hasattr(os, "fchmod"):
        pytest.skip("POSIX-only: fchmod unavailable")
    destination = tmp_path / "download.bin"
    destination.write_bytes(b"old")
    destination.chmod(0o600)
    stream = DownloadStream([b"new"])
    transport = DownloadTransport(stream)
    fs = AsyncFileSystemService(transport)

    await fs.download_file("/remote.bin", destination)

    assert destination.read_bytes() == b"new"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


@pytest.mark.anyio
async def test_download_file_new_destination_keeps_umask_mode(tmp_path: Path) -> None:
    if not hasattr(os, "fchmod"):
        pytest.skip("POSIX-only: fchmod unavailable")
    destination = tmp_path / "download.bin"
    stream = DownloadStream([b"new"])
    transport = DownloadTransport(stream)
    fs = AsyncFileSystemService(transport)

    await fs.download_file("/remote.bin", destination)

    mask = os.umask(0)
    os.umask(mask)
    assert stat.S_IMODE(destination.stat().st_mode) == (0o666 & ~mask)


@pytest.mark.anyio
async def test_download_file_rejects_declared_content_length_mismatch_and_removes_temp(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "download.bin"
    stream = DownloadStream([b"ab"], headers={"Content-Length": "3"})
    transport = DownloadTransport(stream)
    fs = AsyncFileSystemService(transport)

    # A truncated transfer is a ConnectionError, as when the socket drops mid-body
    # (the TypeScript SDK raises the same).
    with pytest.raises(v2_errors.ConnectionError, match="Content-Length") as exc_info:
        await fs.download_file("/remote.bin", destination)
    assert (exc_info.value.status_code, exc_info.value.code, exc_info.value.source) == (None, None, "SDK")

    assert not destination.exists()
    assert list(tmp_path.glob(".daytona-dl-*")) == []
    assert stream.closed is True


@pytest.mark.anyio
async def test_concurrent_downloads_to_same_destination_use_distinct_temp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "download.bin"
    created_paths: list[Path] = []
    release = asyncio.Event()
    started = [asyncio.Event(), asyncio.Event()]

    from daytona.v2 import filesystem as filesystem_module

    real_open_staging = filesystem_module._open_download_staging_file

    def open_staging(destination_path: Path):
        path, handle = real_open_staging(destination_path)
        created_paths.append(path)
        return path, handle

    monkeypatch.setattr(filesystem_module, "_open_download_staging_file", open_staging)

    class CoordinatedStream(DownloadStream):
        def __init__(self, index: int, chunk: bytes) -> None:
            super().__init__([chunk])
            self._index = index

        async def iter_bytes(self):
            started[self._index].set()
            await release.wait()
            for chunk in self._chunks:
                yield chunk

    class SequencedDownloadTransport:
        def __init__(self) -> None:
            self.specs: list[RequestSpec] = []
            self._streams = [CoordinatedStream(0, b"one"), CoordinatedStream(1, b"two")]

        async def open_stream(self, spec: RequestSpec) -> DownloadStream:
            self.specs.append(spec)
            return self._streams.pop(0)

    transport = SequencedDownloadTransport()
    fs = AsyncFileSystemService(transport)  # type: ignore[arg-type]
    first = asyncio.create_task(fs.download_file("/remote.bin", destination))
    second = asyncio.create_task(fs.download_file("/remote.bin", destination))
    await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), timeout=1)
    release.set()
    await asyncio.gather(first, second)

    assert len(created_paths) == 2
    assert len(set(created_paths)) == 2
    assert all(path.parent == tmp_path for path in created_paths)
    assert all(path.name.startswith(".daytona-dl-") for path in created_paths)
    assert not destination.with_name("download.bin.part").exists()


@pytest.mark.anyio
async def test_write_helpers_forward_atomic_query() -> None:
    transport = SendTransport([None, None, {"files": [], "errors": []}])
    fs = AsyncFileSystemService(transport)

    await fs.write_bytes("/remote.bin", b"abc", atomic=True)
    await fs.write_text("/remote.txt", "hello")
    await fs.write_many({"/one": b"1"}, atomic=True)

    assert transport.specs[0].params == {"path": "/remote.bin", "atomic": True}
    assert transport.specs[1].params == {"path": "/remote.txt"}
    assert transport.specs[2].params == {"atomic": True}


def test_atomic_upload_unsupported_error_is_raised_from_toolbox_envelope() -> None:
    with pytest.raises(AtomicUploadUnsupportedError):
        _decode_response(
            httpx.Response(
                409,
                json={
                    "message": "atomic upload is not supported on this filesystem",
                    "code": "ATOMIC_UPLOAD_UNSUPPORTED",
                    "source": "DAYTONA_DAEMON",
                },
            )
        )


@pytest.mark.anyio
async def test_download_file_staging_is_private_during_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The staging file must be created with mode 0o600 (private) while bytes stream."""
    if not hasattr(os, "fchmod"):
        pytest.skip("POSIX-only: fchmod unavailable")
    from daytona.v2 import filesystem as filesystem_module

    staging_modes: list[int] = []
    real_open_staging = filesystem_module._open_download_staging_file

    def patched(dest: Path) -> tuple[Path, object]:
        path, handle = real_open_staging(dest)
        staging_modes.append(stat.S_IMODE(path.stat().st_mode))
        return path, handle

    monkeypatch.setattr(filesystem_module, "_open_download_staging_file", patched)
    stream = DownloadStream([b"content"])
    transport = DownloadTransport(stream)
    fs = AsyncFileSystemService(transport)

    await fs.download_file("/remote.bin", tmp_path / "out.bin")

    assert staging_modes == [0o600]


@pytest.mark.anyio
async def test_download_file_skips_fchmod_when_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """os.fchmod must be guarded so download_file() works on Windows."""
    monkeypatch.delattr(os, "fchmod", raising=False)

    destination = tmp_path / "download.bin"
    destination.write_bytes(b"old")
    stream = DownloadStream([b"new"])
    transport = DownloadTransport(stream)
    fs = AsyncFileSystemService(transport)

    await fs.download_file("/remote.bin", destination)

    assert destination.read_bytes() == b"new"


def transport_payloads(specs: list[RequestSpec]) -> list[tuple[str, str, object]]:
    return [(spec.method, spec.path, spec.json_body) for spec in specs]
