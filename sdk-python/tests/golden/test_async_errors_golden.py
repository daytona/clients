# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest
from daytona_toolbox_api_client_async.exceptions import BadRequestException

pytestmark = [pytest.mark.e2e, pytest.mark.golden, pytest.mark.asyncio(loop_scope="module")]


async def test_async_code_run_unsupported_language_exposes_raw_generated_exception_shape(
    async_code_run_sandboxes,
) -> None:
    python_sandbox = async_code_run_sandboxes["python"]
    original_language = python_sandbox.process._language
    python_sandbox.process._language = "cobol"

    try:
        with pytest.raises(BadRequestException) as exc_info:
            await python_sandbox.process.code_run("print(1)")
    finally:
        python_sandbox.process._language = original_language

    assert exc_info.value.status == 400
    assert exc_info.value.reason == "Bad Request"
    assert '"source":"DAYTONA_DAEMON"' in exc_info.value.body
    assert '"code":"BAD_REQUEST"' in exc_info.value.body
