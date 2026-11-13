# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from array import array
from pathlib import Path

import httpx
import pytest
import respx

from daytona.v2._transport import AsyncTransport
from daytona.v2.errors import BadRequestError
from daytona.v2.filesystem import AsyncFileSystemService
from daytona.v2.git import AsyncGitService

PATH_CALLS = [
    ("read_bytes", (), {}),
    ("read_text", (), {}),
    ("write_bytes", (b"x",), {}),
    ("write_text", ("x",), {}),
    ("presigned_download_url", (), {}),
    ("presigned_upload_url", (), {}),
    ("list", (), {}),
    ("stat", (), {}),
    ("mkdir", (), {}),
    ("remove", (), {}),
    ("move", ("/destination",), {}),
    ("grep", ("x",), {}),
    ("glob", ("*",), {}),
    ("chmod", (), {}),
    ("watch_dir", (lambda _event: None,), {}),
    ("download_file", ("local",), {}),
]


@pytest.mark.anyio
@pytest.mark.parametrize("method,args,kwargs", PATH_CALLS)
@pytest.mark.parametrize("path", ["relative", ".", "", None, 42, Path("/a")])
async def test_fs_refuses_invalid_paths_before_request(method, args, kwargs, path):
    # Given
    async with httpx.AsyncClient() as client:
        transport = AsyncTransport("https://toolbox.invalid", token="token", client=client)
        fs = AsyncFileSystemService(transport)
        with respx.mock as router:
            # When / Then
            with pytest.raises(BadRequestError, match="path|source") as raised:
                await getattr(fs, method)(path, *args, **kwargs)
            assert (raised.value.status_code, raised.value.code, raised.value.source) == (
                400,
                "BAD_REQUEST",
                "SDK",
            )
            assert not router.calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    "method,args,kwargs,parameter",
    [
        ("move", ("/a", "relative"), {}, "destination"),
        ("upload_file", ("missing-local", "relative"), {}, "remote_path"),
        ("upload_file", (b"hello", "/remote.txt"), {}, "local_path must be a path"),
        ("write_many", ({"relative": b"x"},), {}, "files"),
        ("read_many", (["/a", "relative"],), {}, "paths"),
        ("replace", (["/a", "relative"], "x", "y"), {}, "files"),
        ("write_bytes", ("/a", "text"), {}, "content"),
        ("write_bytes", ("/a", [1, 2]), {}, "content"),
        ("write_text", ("/a", 42), {}, "content"),
        ("write_text", ("/a", None), {}, "content"),
        ("write_many", ({"/a": "text"},), {}, "files"),
        ("write_many", ([("/a", b"x")],), {}, "files"),
        ("write_many", ({42: b"x"},), {}, "files"),
        ("list", ("/",), {"depth": "2"}, "depth"),
        ("list", ("/",), {"depth": True}, "depth"),
        ("watch_dir", ("/", None), {}, "on_event"),
        ("watch_dir", ("/", lambda _event: None), {"recursive": "yes"}, "recursive"),
    ],
)
async def test_fs_refuses_invalid_inputs_before_request(method, args, kwargs, parameter):
    # Given
    async with httpx.AsyncClient() as client:
        transport = AsyncTransport("https://toolbox.invalid", token="token", client=client)
        with respx.mock as router:
            # When / Then
            with pytest.raises(BadRequestError, match=parameter):
                await getattr(AsyncFileSystemService(transport), method)(*args, **kwargs)
            assert not router.calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    "method,files",
    [
        (method, files)
        for method in ["read_many", "replace", "add", "restore", "reset"]
        for files in ["/a", b"/a", None, 42, {"/a"}, ["/a", 42]]
        if method != "reset" or files is not None
    ],
)
async def test_path_sequences_refuse_wrong_types_before_request(files, method):
    # Given
    async with httpx.AsyncClient() as client:
        transport = AsyncTransport("https://toolbox.invalid", token="token", client=client)
        fs = AsyncFileSystemService(transport)
        git = AsyncGitService(transport)
        calls = {
            "read_many": lambda: fs.read_many(files),
            "replace": lambda: fs.replace(files, "x", "y"),
            "add": lambda: git.add("repo", files),
            "restore": lambda: git.restore("repo", files),
            "reset": lambda: git.reset("repo", files=files),
        }
        with respx.mock as router:
            # When / Then
            with pytest.raises(BadRequestError, match="files|paths"):
                await calls[method]()
            assert not router.calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    "content", [bytearray(b"xyz"), memoryview(b"xyz"), memoryview(b"x0y0z")[::2], array("B", b"xyz")]
)
@pytest.mark.parametrize("bulk", [False, True])
async def test_writes_preserve_buffer_bytes(content, bulk):
    # Given
    async with httpx.AsyncClient() as client:
        transport = AsyncTransport("https://toolbox.invalid", token="token", client=client)
        fs = AsyncFileSystemService(transport)
        route = "/files/bulk-upload" if bulk else "/files/upload"
        with respx.mock as router:
            request = router.post("https://toolbox.invalid" + route).mock(
                return_value=httpx.Response(200, json={"files": [], "errors": []})
            )
            # When
            if bulk:
                await fs.write_many({"/a": content})
            else:
                await fs.write_bytes("/a", content)
            # Then
            assert b"\r\n\r\nxyz\r\n" in request.calls[0].request.content
