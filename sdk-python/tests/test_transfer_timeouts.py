# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""File transfers are not cut by the request timeout, through each public call.

Every call below runs against a toolbox that takes longer than the client's
request timeout (0.1 s) to answer, so each would fail with
``ClientTimeoutError`` if its transfer marker were dropped. An ordinary call
on the same client is cut, which proves the timeout is armed. The TypeScript
SDK's tests/transfer-timeouts.test.ts holds its transfers to the same rule.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator, Sequence

import httpx
import pytest
import respx
from _handles import lazy_sandbox

from daytona import AsyncDaytona, Daytona, DaytonaConfig
from daytona.v2 import ClientTimeoutError

TOOLBOX = "http://toolbox.invalid"
TIMEOUT = 0.1
SLOW = 0.35


async def _late(payload: object) -> httpx.Response:
    await asyncio.sleep(SLOW)
    return httpx.Response(200, json=payload)


def _trickle(
    chunks: list[bytes] = [b"x"] * 7, content_type: str = "application/octet-stream"
) -> httpx.Response:
    async def body() -> AsyncIterator[bytes]:
        for chunk in chunks:
            await asyncio.sleep(SLOW / len(chunks))
            yield chunk

    return httpx.Response(200, content=body(), headers={"content-type": content_type})


async def _late_trickle(chunks: Sequence[bytes], content_type: str) -> httpx.Response:
    # Headers late too: a streamed download is otherwise bounded until its
    # headers arrive, so only a late start proves its marker.
    await asyncio.sleep(SLOW)
    return _trickle(chunks, content_type)


_BOUNDARY = "transfer-boundary"
_MULTIPART = (
    f'--{_BOUNDARY}\r\nContent-Disposition: form-data; name="file"; filename="/a"\r\n'
    f"Content-Type: application/octet-stream\r\n\r\nxyz\r\n--{_BOUNDARY}--\r\n"
).encode()


def _mock_toolbox(router: respx.MockRouter) -> None:
    router.post(url__regex=rf"{TOOLBOX}/files/upload.*").mock(side_effect=lambda _r: _late({}))
    router.post(url__regex=rf"{TOOLBOX}/files/bulk-upload.*").mock(
        side_effect=lambda _r: _late(
            {"files": [{"path": "/a", "bytes": 1}, {"path": "/b", "bytes": 1}], "errors": []}
        )
    )
    router.get(url__regex=rf"{TOOLBOX}/computeruse/recordings/rec-1/download.*").mock(
        side_effect=lambda _r: _trickle()
    )
    router.get(url__regex=rf"{TOOLBOX}/files/download.*").mock(
        side_effect=lambda _r: _late_trickle([b"d"] * 7, "application/octet-stream")
    )
    router.post(url__regex=rf"{TOOLBOX}/files/bulk-download.*").mock(
        side_effect=lambda _r: _late_trickle(
            [_MULTIPART[i : i + 16] for i in range(0, len(_MULTIPART), 16)],
            f"multipart/form-data; boundary={_BOUNDARY}",
        )
    )
    router.get(url__regex=rf"{TOOLBOX}/files\?.*").mock(side_effect=lambda _r: _late({"items": []}))


@pytest.mark.anyio
async def test_async_transfers_outlast_the_request_timeout(tmp_path: Path) -> None:
    local = tmp_path / "local.bin"
    local.write_bytes(b"payload")
    with respx.mock(assert_all_called=False) as router:
        _mock_toolbox(router)
        client = AsyncDaytona(DaytonaConfig(api_key="k", request_timeout=TIMEOUT)).v2
        try:
            sandbox = lazy_sandbox(client, "sbx", toolbox_url=TOOLBOX, access_token="token")
            await sandbox.fs.write_bytes("/a", b"data")
            result = await sandbox.fs.write_many({"/a": b"1", "/b": b"2"})
            assert [file.path for file in result.files] == ["/a", "/b"]
            await sandbox.fs.upload_file(local, "/remote.bin")
            assert await sandbox.computer_use.recording.download("rec-1") == b"xxxxxxx"
            await sandbox.fs.download_file("/remote.bin", tmp_path / "down.bin")
            assert (tmp_path / "down.bin").read_bytes() == b"ddddddd"
            assert (await sandbox.fs.read_many(["/a"])).files["/a"] == b"xyz"
            # The same client cuts an ordinary call at the request timeout.
            with pytest.raises(ClientTimeoutError):
                await sandbox.fs.list("/")
        finally:
            await client.close()


def test_sync_transfers_outlast_the_request_timeout(tmp_path: Path) -> None:
    local = tmp_path / "local.bin"
    local.write_bytes(b"payload")
    with respx.mock(assert_all_called=False) as router:
        _mock_toolbox(router)
        with Daytona(DaytonaConfig(api_key="k", request_timeout=TIMEOUT)) as root:
            sandbox = lazy_sandbox(root.v2, "sbx", toolbox_url=TOOLBOX, access_token="token")
            sandbox.fs.write_bytes("/a", b"data")
            assert [file.path for file in sandbox.fs.write_many({"/a": b"1", "/b": b"2"}).files] == [
                "/a",
                "/b",
            ]
            sandbox.fs.upload_file(local, "/remote.bin")
            assert sandbox.computer_use.recording.download("rec-1") == b"xxxxxxx"
            sandbox.fs.download_file("/remote.bin", tmp_path / "down.bin")
            assert (tmp_path / "down.bin").read_bytes() == b"ddddddd"
            with pytest.raises(ClientTimeoutError):
                sandbox.fs.list("/")
