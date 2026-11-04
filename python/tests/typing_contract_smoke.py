# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from daytona.v2._core import parse_log_page, parse_process, parse_wait
from daytona.v2._gen.toolbox import ExitResult, LogFrameDto, ProcessDto, ProcessLogsResponse

process_payload = ProcessDto.model_validate(
    {
        "id": "proc-1",
        "kind": "exec",
        "cwd": "/workspace",
        "stdin": "closed",
        "keepLogs": "until_cleanup",
        "state": "running",
        "createdAt": "2026-08-24T00:00:00Z",
        "truncatedHead": False,
    }
)

wait_payload = ExitResult.model_validate(
    {
        "exitCode": 0,
        "signal": None,
        "reason": "exited",
    }
)

logs_payload = ProcessLogsResponse.model_validate(
    {
        "frames": [
            {
                "cursor": "c1",
                "channel": "stdout",
                "timestamp": 1,
                "data": "hello",
            }
        ],
        "nextCursor": "c1",
        "truncatedHead": False,
        "eof": False,
    }
)

frame_payload: LogFrameDto = logs_payload.frames[0]

process_info = parse_process(process_payload)
wait_result = parse_wait(wait_payload)
log_page = parse_log_page(logs_payload)

assert process_info.id == "proc-1"
assert wait_result.exit_code == 0
assert frame_payload.channel == "stdout"
assert log_page.items[0].data == "hello"
