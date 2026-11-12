# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Image, file, interpreter and git failures map to their exported classes;
``git.delete_branch(force=...)``; ``fs.grep()`` / ``fs.glob()`` report the
paths they skipped. Responses use the server's exact shapes."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from daytona.v2 import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    FileAccessDeniedError,
    FileReadFailedError,
    FileSearchResult,
    ForbiddenError,
    GitAuthFailedError,
    GitBranchExistsError,
    GitBranchNotFoundError,
    GitRepoNotFoundError,
    ImageNameReservedError,
    ImageNameTakenError,
    InternalServerError,
    InvalidFilePathError,
    KernelUnavailableError,
    ListResult,
    NotFoundError,
    ServiceUnavailableError,
    UnsupportedOperationError,
)
from daytona.v2._core import error_from_response, parse_bulk_read_result
from daytona.v2._transport import AsyncTransport
from daytona.v2.errors import DaytonaError
from daytona.v2.filesystem import AsyncFileSystemService
from daytona.v2.git import AsyncGitService
from daytona.v2.interpreter import AsyncInterpreterService
from daytona.v2.process import AsyncProcessService
from daytona.v2.types import BulkReadError, JsonValue

_API = "DAYTONA_API"
_DAEMON = "DAYTONA_DAEMON"

_SERVER_ERRORS: list[tuple[int, str, str, str, type[DaytonaError], type[DaytonaError]]] = [
    (
        409,
        "IMAGE_NAME_TAKEN",
        _API,
        "image name 'py312' already exists for different content or import parameters",
        ImageNameTakenError,
        ConflictError,
    ),
    (
        409,
        "IMAGE_NAME_TAKEN",
        _API,
        (
            "'py312' is a machine snapshot in this project; images and snapshots share one name namespace, "
            "so pick another image name"
        ),
        ImageNameTakenError,
        ConflictError,
    ),
    (
        400,
        "IMAGE_NAME_RESERVED",
        _API,
        "image name 'img_x' is reserved: 'img_' is a resource id prefix",
        ImageNameReservedError,
        BadRequestError,
    ),
    (403, "FILE_ACCESS_DENIED", _DAEMON, "/root/x: permission denied", FileAccessDeniedError, ForbiddenError),
    (400, "INVALID_FILE_PATH", _DAEMON, "path must be absolute", InvalidFilePathError, BadRequestError),
    (
        400,
        "INVALID_FILE_PATH",
        _DAEMON,
        "/dev/null: not a regular file",
        InvalidFilePathError,
        BadRequestError,
    ),
    (500, "FILE_READ_FAILED", _DAEMON, "/x: read failed", FileReadFailedError, InternalServerError),
    (
        400,
        "UNSUPPORTED_OPERATION",
        _DAEMON,
        "unsupported language: ruby",
        UnsupportedOperationError,
        BadRequestError,
    ),
    (
        503,
        "KERNEL_UNAVAILABLE",
        _DAEMON,
        "python kernel failed to start",
        KernelUnavailableError,
        ServiceUnavailableError,
    ),
    (401, "GIT_AUTH_FAILED", _DAEMON, "git authentication failed", GitAuthFailedError, AuthenticationError),
    (404, "GIT_REPO_NOT_FOUND", _DAEMON, "repository does not exist", GitRepoNotFoundError, NotFoundError),
    (404, "GIT_BRANCH_NOT_FOUND", _DAEMON, "branch does not exist: x", GitBranchNotFoundError, NotFoundError),
    (409, "GIT_BRANCH_EXISTS", _DAEMON, "branch already exists: x", GitBranchExistsError, ConflictError),
    (
        409,
        "CONFLICT",
        _DAEMON,
        "delete branch failed: error: the branch 'x' is not fully merged",
        ConflictError,
        ConflictError,
    ),
]


@pytest.mark.parametrize(("status", "code", "source", "message", "cls", "category"), _SERVER_ERRORS)
def test_server_codes_map_to_their_exported_classes(
    status: int,
    code: str,
    source: str,
    message: str,
    cls: type[DaytonaError],
    category: type[DaytonaError],
) -> None:
    body = json.dumps({"code": code, "message": message, "source": source}).encode()

    error = error_from_response(status, body, content_type="application/json")

    assert type(error) is cls
    assert isinstance(error, category)
    assert (error.status_code, error.code, error.source) == (status, code, source)
    assert message in str(error)


Handler = Callable[[httpx.Request], httpx.Response]


@asynccontextmanager
async def _transport(handler: Handler) -> AsyncIterator[AsyncTransport]:
    transport = AsyncTransport("http://toolbox.invalid")
    transport._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler), base_url="http://toolbox.invalid"
    )
    try:
        yield transport
    finally:
        await transport.close()


def _json(status: int, payload: JsonValue) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _error(status: int, code: str, message: str) -> httpx.Response:
    return _json(status, {"code": code, "message": message, "source": _DAEMON})


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kwargs", "body"),
    [
        ({}, {"path": "/repo", "name": "feature"}),
        ({"force": False}, {"path": "/repo", "name": "feature"}),
        ({"force": True}, {"path": "/repo", "name": "feature", "force": True}),
    ],
)
async def test_delete_branch_sends_force_only_when_true(
    kwargs: dict[str, bool], body: dict[str, Any]
) -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(204)

    async with _transport(handler) as transport:
        await AsyncGitService(transport).delete_branch("/repo", "feature", **kwargs)

    assert [(request.method, request.url.path) for request in sent] == [("DELETE", "/git/branches")]
    assert json.loads(sent[0].content) == body


@pytest.mark.anyio
@pytest.mark.parametrize("force", [None, 1, "true"])
async def test_delete_branch_refuses_a_force_that_is_not_a_boolean(force: object) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    async with _transport(handler) as transport:
        with pytest.raises(BadRequestError, match="force must be a boolean") as raised:
            await AsyncGitService(transport).delete_branch("/repo", "feature", force=force)  # type: ignore[arg-type]

    assert raised.value.source == "SDK"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("response", "cls"),
    [
        (
            _error(409, "CONFLICT", "delete branch failed: error: the branch 'x' is not fully merged"),
            ConflictError,
        ),
        (_error(404, "GIT_BRANCH_NOT_FOUND", "branch does not exist: x"), GitBranchNotFoundError),
        (_error(404, "GIT_REPO_NOT_FOUND", "repository does not exist"), GitRepoNotFoundError),
    ],
)
async def test_delete_branch_failures_raise_their_classes(
    response: httpx.Response, cls: type[DaytonaError]
) -> None:
    async with _transport(lambda _request: response) as transport:
        with pytest.raises(cls) as raised:
            await AsyncGitService(transport).delete_branch("/repo", "x")

    assert type(raised.value) is cls


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("operation", "response", "cls"),
    [
        ("create_branch", _error(409, "GIT_BRANCH_EXISTS", "branch already exists: x"), GitBranchExistsError),
        (
            "checkout_branch",
            _error(404, "GIT_BRANCH_NOT_FOUND", "checkout failed: x"),
            GitBranchNotFoundError,
        ),
        ("checkout_branch", _error(409, "CONFLICT", "checkout failed: local changes"), ConflictError),
        ("pull", _error(401, "GIT_AUTH_FAILED", "git authentication failed"), GitAuthFailedError),
        ("clone", _error(404, "GIT_REPO_NOT_FOUND", "repository does not exist"), GitRepoNotFoundError),
    ],
)
async def test_git_failures_raise_their_classes(
    operation: str, response: httpx.Response, cls: type[DaytonaError]
) -> None:
    async with _transport(lambda _request: response) as transport:
        git = AsyncGitService(transport)
        calls = {
            "create_branch": lambda: git.create_branch("/repo", "x"),
            "checkout_branch": lambda: git.checkout_branch("/repo", "x"),
            "pull": lambda: git.pull("/repo"),
            "clone": lambda: git.clone("https://example.com/missing.git", "/repo"),
        }
        with pytest.raises(cls) as raised:
            await calls[operation]()

    assert type(raised.value) is cls


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("response", "cls"),
    [
        (_error(400, "UNSUPPORTED_OPERATION", "unsupported language: Python"), UnsupportedOperationError),
        (_error(503, "KERNEL_UNAVAILABLE", "python kernel failed to start"), KernelUnavailableError),
    ],
)
async def test_interpreter_language_failures_raise_their_classes(
    response: httpx.Response, cls: type[DaytonaError]
) -> None:
    async with _transport(lambda _request: response) as transport:
        interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))
        with pytest.raises(cls) as raised:
            await interpreter.create_context(language="Python")

    assert type(raised.value) is cls


_MATCH = {"file": "/w/a.txt", "line": 3, "content": "needle"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "skipped"),
    [
        ({"items": [_MATCH], "skipped": ["/w/private", "/w/secret.txt"]}, ("/w/private", "/w/secret.txt")),
        ({"items": [_MATCH], "skipped": []}, ()),
        ({"items": [_MATCH]}, ()),
        ([_MATCH], ()),
    ],
)
async def test_grep_reports_skipped_paths_and_accepts_the_bare_array(
    payload: JsonValue, skipped: tuple[str, ...]
) -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return _json(200, payload)

    async with _transport(handler) as transport:
        result = await AsyncFileSystemService(transport).grep("/w", "needle")

    assert sent[0].url.path == "/files/find"
    assert isinstance(result, FileSearchResult)
    assert isinstance(result, ListResult)
    assert [(m.file, m.line, m.content) for m in result.items] == [("/w/a.txt", 3, "needle")]
    assert result.skipped == skipped
    assert result.next_cursor is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.skipped = ()  # type: ignore[misc]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "skipped"),
    [
        ({"files": ["/w/a.py"], "skipped": ["/w/private"]}, ("/w/private",)),
        ({"files": ["/w/a.py"], "skipped": []}, ()),
        ({"files": ["/w/a.py"]}, ()),
    ],
)
async def test_glob_reports_skipped_paths(payload: JsonValue, skipped: tuple[str, ...]) -> None:
    async with _transport(lambda _request: _json(200, payload)) as transport:
        result = await AsyncFileSystemService(transport).glob("/w", "*.py")

    assert isinstance(result, FileSearchResult)
    assert result.items == ("/w/a.py",)
    assert result.skipped == skipped


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("operation", "payload", "message"),
    [
        ("grep", {"items": [], "skipped": "/w/private"}, "grep response: skipped must be a list"),
        ("grep", {"items": [], "skipped": [3]}, r"grep response: skipped\[0\] must be a string"),
        ("grep", {"skipped": []}, "grep response: items is required"),
        ("glob", {"files": [], "skipped": {"path": "/w"}}, "glob response: skipped must be a list"),
        ("glob", {"files": [], "skipped": [None]}, r"glob response: skipped\[0\] must be a string"),
    ],
)
async def test_search_responses_that_break_the_contract_are_refused(
    operation: str, payload: JsonValue, message: str
) -> None:
    async with _transport(lambda _request: _json(200, payload)) as transport:
        fs = AsyncFileSystemService(transport)
        with pytest.raises(BadRequestError, match=message) as raised:
            await (fs.grep("/w", "x") if operation == "grep" else fs.glob("/w", "*"))

    assert raised.value.source == "SDK"


@pytest.mark.anyio
async def test_an_unreadable_search_root_raises_file_access_denied() -> None:
    response = _error(403, "FILE_ACCESS_DENIED", "/root: permission denied")
    async with _transport(lambda _request: response) as transport:
        with pytest.raises(FileAccessDeniedError):
            await AsyncFileSystemService(transport).glob("/root", "*")


def test_read_many_keeps_a_per_file_file_read_failed() -> None:
    error = (
        b'{"path":"/w/b","code":"FILE_READ_FAILED","message":"/w/b: read failed","source":"DAYTONA_DAEMON"}'
    )
    body = (
        b"--DAYTONA-FILE-BOUNDARY\r\n"
        b'Content-Disposition: form-data; name="file"; filename="/w/a"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\nhi\r\n"
        b"--DAYTONA-FILE-BOUNDARY\r\n"
        b'Content-Disposition: form-data; name="error"\r\n'
        b"Content-Type: application/json\r\n\r\n" + error + b"\r\n--DAYTONA-FILE-BOUNDARY--\r\n"
    )

    result = parse_bulk_read_result(body)

    assert dict(result.files) == {"/w/a": b"hi"}
    assert result.errors == (
        BulkReadError(
            path="/w/b", message="/w/b: read failed", code="FILE_READ_FAILED", source="DAYTONA_DAEMON"
        ),
    )
