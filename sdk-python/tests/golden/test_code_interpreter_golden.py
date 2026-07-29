# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from daytona.common.errors import DaytonaTimeoutError

from ._helpers import assert_iso8601_z

pytestmark = [pytest.mark.e2e, pytest.mark.golden]


def test_code_interpreter_context_shape_and_lifecycle(sandbox) -> None:
    context = sandbox.code_interpreter.create_context()
    contexts = sandbox.code_interpreter.list_contexts()

    assert context.id
    assert context.cwd == "/home/daytona"
    assert context.active is True
    assert context.language == "python"
    assert_iso8601_z(context.created_at)
    assert any(item.id == context.id for item in contexts)

    sandbox.code_interpreter.delete_context(context)


def test_code_interpreter_run_code_and_persistent_context(sandbox) -> None:
    context = sandbox.code_interpreter.create_context()
    try:
        default_result = sandbox.code_interpreter.run_code("print(1+1)")
        sandbox.code_interpreter.run_code("x = 10", context=context)
        persisted = sandbox.code_interpreter.run_code("print(x)", context=context)
    finally:
        sandbox.code_interpreter.delete_context(context)

    assert default_result.stdout == "2\n"
    assert default_result.stderr == ""
    assert default_result.error is None
    assert persisted.stdout == "10\n"
    assert persisted.stderr == ""
    assert persisted.error is None


def test_code_interpreter_error_shape_and_timeout(sandbox) -> None:
    error = sandbox.code_interpreter.run_code('raise ValueError("bad")')

    assert error.stdout == ""
    assert error.stderr == ""
    assert error.error is not None
    assert error.error.name == "ValueError"
    assert error.error.value == "bad"
    assert "ValueError: bad" in error.error.traceback

    with pytest.raises(DaytonaTimeoutError) as exc_info:
        sandbox.code_interpreter.run_code("import time; time.sleep(2)", timeout=1)

    assert str(exc_info.value) == (
        "Failed to run code: Execution timed out: operation exceeded the configured `timeout`. "
        "Provide a larger value if needed."
    )
