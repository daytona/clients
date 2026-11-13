# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Parsed result collections are immutable, including nested accessibility nodes."""

from __future__ import annotations

import json

import httpx
import pytest

from daytona.v2._core import (
    expect_process_dto,
    expect_status_response,
    parse_bulk_read_result,
    parse_bulk_write_result,
    parse_log_page,
    parse_process,
)
from daytona.v2._transport import AsyncTransport
from daytona.v2.computer_use_core import parse_computer_use_a11y_find_payload
from daytona.v2.interpreter import AsyncInterpreterService
from daytona.v2.process import AsyncProcessService
from daytona.v2.types import JsonObject


@pytest.mark.anyio
async def test_parsed_result_collections_are_tuples_and_hashable_without_dicts() -> None:
    # Given representative wire responses, including siblings and grandchildren.
    process: JsonObject = {
        "id": "proc",
        "kind": "exec",
        "state": "terminal",
        "reason": "exited",
        "exitCode": 0,
        "cwd": "/",
        "argv": ["echo", "ok"],
        "stdin": "closed",
        "keepLogs": "on_exit_ttl",
        "createdAt": "2026-01-01T00:00:00Z",
        "truncatedHead": False,
    }
    page: JsonObject = {
        "frames": [{"cursor": "1", "channel": "stdout", "data": "ok", "timestamp": 1}],
        "nextCursor": "1",
        "truncatedHead": False,
        "eof": True,
    }
    error_page: JsonObject = {
        **page,
        "frames": [
            {
                "cursor": "0",
                "channel": "execute_result",
                "data": json.dumps({"data": {"text/plain": "value"}}),
                "timestamp": 1,
            },
            {
                "cursor": "1",
                "channel": "error",
                "data": json.dumps({"ename": "Error", "evalue": "bad", "traceback": ["line"]}),
                "timestamp": 1,
            },
        ],
    }
    node: JsonObject = {
        "id": "node",
        "role": "button",
        "name": "OK",
        "bounds": {"x": 0, "y": 0, "width": 1, "height": 1},
        "states": ["enabled"],
        "actions": ["press"],
    }
    responses = iter(
        [
            {**process, "logs": page},
            {**process, "kind": "code"},
            {"reason": "exited", "exitCode": 0},
            error_page,
        ]
    )

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    # When the SDK parses responses and collects execution results.
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        transport = AsyncTransport("https://toolbox.invalid", client=client)
        processes = AsyncProcessService("sbx", transport)
        run = await processes.run("echo ok")
        interpreted = await AsyncInterpreterService("sbx", transport, processes).run("raise Error()")
    log_page = parse_log_page(page)
    info = parse_process(expect_process_dto(process))
    bulk = parse_bulk_write_result({"files": [{"path": "/a", "bytes": 1}], "errors": ["failed"]})
    read = parse_bulk_read_result(b"--boundary--\r\n", content_type="multipart/mixed; boundary=boundary")
    status = expect_status_response(
        {
            "currentBranch": "main",
            "ahead": 0,
            "behind": 0,
            "branchPublished": True,
            "fileStatus": [{"name": "a", "staging": "M", "worktree": " ", "extra": ""}],
        }
    )
    found = parse_computer_use_a11y_find_payload(
        {
            "matches": [
                {
                    **node,
                    "children": [
                        {**node, "id": "first", "children": [{**node, "id": "grandchild"}]},
                        {**node, "id": "second", "children": []},
                    ],
                },
                {**node, "id": "last"},
            ],
            "truncated": False,
        }
    )

    # Then all collection fields are tuples, order/optional children are preserved,
    # and results without dict fields can be used as hash keys.
    assert interpreted.error is not None
    root, last = found.matches
    assert root.children is not None
    first, second = root.children
    assert first.children is not None
    for collection in (
        log_page.items,
        info.argv,
        run.logs,
        interpreted.results,
        interpreted.error.traceback,
        bulk.files,
        bulk.errors,
        read.errors,
        status.file_status,
        found.matches,
        root.states,
        root.actions,
        root.children,
        first.children,
        second.children,
    ):
        assert isinstance(collection, tuple)
    assert [child.id for child in root.children] == ["first", "second"]
    assert first.children[0].id == "grandchild"
    assert last.id == "last" and last.children is None
    assert interpreted.error.traceback == ("line",)
    assert interpreted.results[0].text == "value"
    assert isinstance(interpreted.results[0].data, dict)
    assert isinstance(read.files, dict)
    for model in (
        log_page,
        info,
        run,
        interpreted.process,
        interpreted.error,
        bulk,
        status,
        found,
    ):
        assert isinstance(hash(model), int)
