# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Interpreter charts: a matplotlib figure's data next to its image.

The cases are shared with the TypeScript SDK's
``sdk-typescript/tests/interpreter-charts.test.ts``
(``fixtures/interpreter-charts.json``): the same display frames must give the
same charts and results in both SDKs.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Optional

import pytest

from daytona.v2 import (
    AsyncInterpreterService,
    AsyncProcessService,
    BarChart,
    Chart,
    CursorExpiredError,
    LineChart,
    UnknownChart,
)
from daytona.v2._core import RequestSpec
from daytona.v2.charts import _parse_chart
from daytona.v2.errors import DaytonaError, create_error
from daytona.v2.interpreter import _outputs
from daytona.v2.process import TIMED_OUT_LOG_PAGE_LIMIT, _all_logs
from daytona.v2.types import LogFrame, LogPage

_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "interpreter-charts.json").read_text())
_CHART = "application/vnd.daytona.chart.v1+json"
_DISPLAY = "application/vnd.daytona.display.v1+json"


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def _wire_shape(value: object) -> object:
    """A chart as the TypeScript SDK returns it: camelCase, absent fields
    omitted, tuples as lists."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            _camel(field.name): _wire_shape(getattr(value, field.name))
            for field in dataclasses.fields(value)
            if getattr(value, field.name) is not None
        }
    if isinstance(value, (tuple, list)):
        return [_wire_shape(item) for item in value]
    return value


@pytest.mark.parametrize("case", _FIXTURE["charts"], ids=lambda case: case["name"])
def test_a_chart_parses_as_both_sdks_agree(case: dict[str, Any]) -> None:
    chart = _parse_chart(case["wire"])
    if case["expected"] is None:
        assert chart is None
    else:
        assert _wire_shape(chart) == case["expected"]


def _frames(case: dict[str, Any]) -> list[LogFrame]:
    return [
        LogFrame(cursor=str(i), channel=frame["channel"], data=frame["data"], timestamp_ms=i)
        for i, frame in enumerate(case["frames"])
    ]


@pytest.mark.parametrize("case", _FIXTURE["runs"], ids=lambda case: case["name"])
def test_a_runs_outputs_and_charts_match_the_shared_cases(case: dict[str, Any]) -> None:
    results = _outputs(_frames(case))
    expected = case["expected"]
    assert len(results) == len(expected["results"])
    for output, want in zip(results, expected["results"]):
        assert output.is_main_result is want["isMainResult"]
        assert sorted(output.data) == want["dataKeys"]
        assert (output.chart.type if output.chart is not None else None) == want["chartType"]
        if "bars" in want:
            assert isinstance(output.chart, BarChart) and len(output.chart.elements) == want["bars"]
        if "text" in want:
            assert output.text == want["text"]
        if "png" in want:
            assert output.png == want["png"]
        if "rawChart" in want:
            assert output.data[_CHART] == want["rawChart"]
    charts = [output.chart for output in results if output.chart is not None]
    assert [chart.type for chart in charts] == expected["charts"]


def test_charts_are_frozen_tuples() -> None:
    chart = _parse_chart(_FIXTURE["charts"][0]["wire"])
    assert isinstance(chart, LineChart)
    assert isinstance(chart.elements, tuple) and isinstance(chart.elements[0].points[0], tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        chart.title = "x"  # type: ignore[misc]


def test_an_unknown_chart_type_is_never_an_error() -> None:
    chart: Chart | None = _parse_chart({"type": "sankey", "flows": []})
    assert chart == UnknownChart(raw_type="sankey")
    # A union a caller narrows by `type` without a catch-all still compiles:
    # every chart has one.
    assert chart is not None and chart.type == "unknown"


class _Transport:
    """Answers the interpreter's requests from a queue; an exception raises."""

    request_timeout_seconds = 30.0
    max_response_bytes = 0

    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.paths: list[str] = []

    async def send(self, spec: RequestSpec) -> object:
        cursor = spec.params.get("cursor")
        self.paths.append(f"{spec.method} {spec.path}" + (f"?cursor={cursor}" if cursor is not None else ""))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        pass


def _process(process_id: str) -> dict[str, object]:
    return {
        "id": process_id,
        "kind": "code",
        "state": "running",
        "createdAt": "2026-01-01T00:00:00Z",
        "cwd": "/",
        "stdin": "closed",
        "keepLogs": "on_exit_ttl",
        "truncatedHead": False,
        "language": "python",
    }


def _page(frames: list[dict[str, object]], cursor: str, *, eof: bool = True) -> dict[str, object]:
    return {"frames": frames, "nextCursor": cursor, "eof": eof, "truncatedHead": False}


def _frame(cursor: str, channel: str, data: object) -> dict[str, object]:
    return {
        "cursor": cursor,
        "channel": channel,
        "timestamp": 1,
        "data": data if isinstance(data, str) else json.dumps(data),
    }


@pytest.mark.anyio
async def test_a_run_returns_its_charts_and_the_final_state_of_each_display() -> None:
    one = {"type": "bar", "orientation": "vertical", "elements": [{"label": "a", "value": 1}]}
    two = {
        "type": "bar",
        "orientation": "vertical",
        "elements": [{"label": "a", "value": 1}, {"label": "b", "value": 2}],
    }
    transport = _Transport(
        [
            _process("p1"),
            {"exitCode": 0, "reason": "exited"},
            _page(
                [
                    _frame("1", "execute_result", {"text/plain": "<Axes: >"}),
                    _frame(
                        "2",
                        "display",
                        {"image/png": "A", _CHART: one, _DISPLAY: {"displayId": "d", "update": False}},
                    ),
                    _frame(
                        "3",
                        "display",
                        {"image/png": "B", _CHART: two, _DISPLAY: {"displayId": "d", "update": True}},
                    ),
                ],
                "3",
            ),
        ]
    )
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]

    result = await interpreter.run("df.plot()")

    assert [output.text for output in result.results] == ["<Axes: >", None]
    figure = result.results[1]
    assert figure.png == "B" and _DISPLAY not in figure.data
    assert isinstance(figure.chart, BarChart) and [bar.label for bar in figure.chart.elements] == ["a", "b"]
    assert result.charts == (figure.chart,)


@pytest.mark.anyio
async def test_a_run_whose_log_head_was_evicted_returns_what_the_sandbox_kept() -> None:
    expired = create_error(
        "the requested position was evicted by log retention",
        409,
        "CURSOR_EXPIRED",
        "DAYTONA_DAEMON",
        first_available_cursor="c9",
    )
    transport = _Transport(
        [
            _process("p1"),
            {"exitCode": 0, "reason": "exited"},
            expired,
            _page([_frame("c10", "display", {"text/plain": "<Figure>", _CHART: {"type": "unknown"}})], "c10"),
        ]
    )
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]

    result = await interpreter.run("plot()")

    assert [chart.type for chart in result.charts] == ["unknown"]
    assert transport.paths[-2:] == ["GET /processes/p1/logs", "GET /processes/p1/logs?cursor=c9"]


@pytest.mark.anyio
async def test_an_expiry_without_a_resume_point_still_raises() -> None:
    expired = create_error("evicted", 409, "CURSOR_EXPIRED", "DAYTONA_DAEMON")
    transport = _Transport([_process("p1"), {"exitCode": 0, "reason": "exited"}, expired])
    interpreter = AsyncInterpreterService("sbx", transport, AsyncProcessService("sbx", transport))  # type: ignore[arg-type]

    with pytest.raises(CursorExpiredError):
        await interpreter.run("plot()")


class _ExpiringLogs:
    """A process handle whose log reads answer from a queue: an exception
    raises, anything else is the page."""

    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.cursors: list[Optional[str]] = []

    async def logs(
        self, *, cursor: Optional[str] = None, limit: Optional[int] = None, encoding: str = "text"
    ) -> LogPage:
        self.cursors.append(cursor)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, LogPage)
        return outcome


def _expired(resume: Optional[str]) -> DaytonaError:
    return create_error(
        "the requested position was evicted by log retention",
        409,
        "CURSOR_EXPIRED",
        "DAYTONA_DAEMON",
        first_available_cursor=resume,
    )


@pytest.mark.anyio
async def test_a_bounded_read_whose_pages_keep_expiring_stops_at_its_page_budget() -> None:
    # A timed-out run reads at most TIMED_OUT_LOG_PAGE_LIMIT pages; an expired
    # read is one of them, so retention racing a live process cannot loop.
    handle = _ExpiringLogs([_expired(f"c{i}") for i in range(1, 50)])
    frames = await _all_logs(handle, max_pages=TIMED_OUT_LOG_PAGE_LIMIT)  # type: ignore[arg-type]
    assert frames == []
    assert handle.cursors == [None, *(f"c{i}" for i in range(1, TIMED_OUT_LOG_PAGE_LIMIT))]


@pytest.mark.anyio
async def test_an_expiry_that_names_the_cursor_just_read_raises() -> None:
    handle = _ExpiringLogs([_expired("c5"), _expired("c5")])
    with pytest.raises(CursorExpiredError):
        await _all_logs(handle)  # type: ignore[arg-type]
    assert handle.cursors == [None, "c5"]


@pytest.mark.anyio
async def test_a_resume_point_already_read_from_raises() -> None:
    # A sandbox answering A with "resume at B" and B with "resume at A" must
    # not keep a read going forever: retention only moves forward.
    handle = _ExpiringLogs([_expired("cB"), _expired("cA"), _expired("cB"), _expired("cA")])
    with pytest.raises(CursorExpiredError):
        await _all_logs(handle)  # type: ignore[arg-type]
    assert handle.cursors == [None, "cB", "cA"]


@pytest.mark.anyio
async def test_a_process_run_whose_log_head_was_evicted_returns_what_the_sandbox_kept() -> None:
    # The inline page is not the end, so run() reads the log from the start,
    # which retention evicted.
    created = dict(
        _process("p1"),
        kind="exec",
        state="terminal",
        exitCode=0,
        reason="exited",
        logs=_page([_frame("c1", "stdout", "early")], "c1", eof=False),
    )
    created.pop("language")
    transport = _Transport([created, _expired("c7"), _page([_frame("c8", "stdout", "late")], "c8")])
    service = AsyncProcessService("sbx", transport)  # type: ignore[arg-type]

    result = await service.run("echo")

    assert result.stdout == "late"
    assert transport.paths[-2:] == ["GET /processes/p1/logs", "GET /processes/p1/logs?cursor=c7"]
