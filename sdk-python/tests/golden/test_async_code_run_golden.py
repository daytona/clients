# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from daytona.common.process import CodeRunParams

pytestmark = [pytest.mark.e2e, pytest.mark.golden, pytest.mark.asyncio(loop_scope="module")]


async def test_async_code_run_python_and_process_exit_from_stderr(async_code_run_sandboxes) -> None:
    python_sandbox = async_code_run_sandboxes["python"]

    success = await python_sandbox.process.code_run("print(1+1)")
    error = await python_sandbox.process.code_run('import sys; sys.stderr.write("E\\n"); raise SystemExit(4)')

    assert success.exit_code == 0
    assert success.result == "2\n"
    assert error.exit_code == 4
    assert error.result == "E\n"


async def test_async_code_run_javascript_and_typescript(async_code_run_sandboxes) -> None:
    javascript_sandbox = async_code_run_sandboxes["javascript"]
    typescript_sandbox = async_code_run_sandboxes["typescript"]

    javascript = await javascript_sandbox.process.code_run("console.log(41+1)")
    typescript = await typescript_sandbox.process.code_run("const x: number = 5; console.log(x)")

    assert javascript.exit_code == 0
    assert javascript.result == "42\n"
    assert typescript.exit_code == 0
    assert typescript.result.startswith("5\n")


async def test_async_code_run_passes_argv_and_env(async_code_run_sandboxes) -> None:
    python_sandbox = async_code_run_sandboxes["python"]
    result = await python_sandbox.process.code_run(
        'import os,sys;print(sys.argv[1:], os.environ.get("Z"))',
        params=CodeRunParams(argv=["a", "b"], env={"Z": "z"}),
    )

    assert result.exit_code == 0
    assert result.result == "['a', 'b'] z\n"


async def test_async_code_run_matplotlib_show_returns_chart_artifact_shape(async_code_run_sandboxes) -> None:
    python_sandbox = async_code_run_sandboxes["python"]
    result = await python_sandbox.process.code_run(
        'import matplotlib\nmatplotlib.use("Agg")\nimport matplotlib.pyplot as plt\nplt.plot([1,2,3])\nplt.show()'
    )

    assert result.exit_code == 0
    assert result.result == ""
    assert result.artifacts is not None
    assert result.artifacts.charts is not None
    assert len(result.artifacts.charts) == 1
    chart = result.artifacts.charts[0]
    assert chart.type == "line"
    assert chart.png.startswith("iVBORw0KGgo")
    assert chart.x_ticks
    assert chart.elements
