#!/usr/bin/env python3
"""charts — read a matplotlib figure's data, not just its image.

Installs matplotlib in the sandbox, draws a figure with two subplots in the
interpreter, and reads each subplot's chart from the run's result.

Run (from examples):
    DAYTONA_API_KEY=dtn_... \\
      python python/charts.py
"""

from __future__ import annotations

import sys

from _common import RUN, client, failed, info, require_started, sandbox_image, section

from daytona.v2 import BarChart, Chart, CompositeChart, LineChart, Sandbox

FIGURE = """
import matplotlib.pyplot as plt
fig, (left, right) = plt.subplots(1, 2, figsize=(8, 3))
left.plot([1, 2, 3, 4], [10, 20, 15, 30], label="visits")
left.set_title("traffic")
right.bar(["a", "b", "c"], [3, 7, 5])
right.set_title("signups")
plt.show()
"""


def describe(chart: Chart) -> str:
    """One line about a chart: its type, title and data."""
    match chart:
        case LineChart():
            series = ", ".join(f"{s.label or 'series'}: {len(s.points)} points" for s in chart.elements)
            return f"line '{chart.title}' ({series})"
        case BarChart():
            bars = ", ".join(f"{bar.label}={bar.value}" for bar in chart.elements)
            return f"bar '{chart.title}' ({bars})"
        case _:
            return f"{chart.type} '{chart.title}'"


def read_charts(sandbox: Sandbox) -> bool:
    """Draw the figure; return whether its chart and image came back."""
    section("install matplotlib")
    install = sandbox.process.run("python3 -m pip install --quiet matplotlib")
    if install.exit_code != 0:
        failed(f"pip install failed (exit={install.exit_code}): {install.output.strip()[-300:]}")
        return False

    section("draw a figure and read its chart")
    result = sandbox.interpreter.run(FIGURE, language="python")
    if result.error is not None or result.process.exit_code != 0:
        detail = f"{result.error.name}: {result.error.value}" if result.error is not None else "no error output"
        failed(f"the figure failed (exit={result.process.exit_code}, {detail})")
        return False
    # Every figure the run displayed; prefer it to result.results, where a
    # pandas or seaborn call puts its axes first.
    if len(result.charts) != 1 or not isinstance(result.charts[0], CompositeChart):
        failed(f"expected one figure with subplots, got {[chart.type for chart in result.charts]}")
        return False
    figure = result.charts[0]
    for subplot in figure.elements:
        info(describe(subplot))
    kinds = [subplot.type for subplot in figure.elements]
    if kinds != ["line", "bar"]:
        failed(f"expected a line and a bar subplot, got {kinds}")
        return False
    # The figure's image is in the same output as its chart.
    image = next((output.png for output in result.results if output.chart is figure), None)
    if not image:
        failed("the figure's image is missing")
        return False
    info(f"image: {len(image)} base64 characters of PNG")
    return True


def main() -> int:
    daytona = client()

    sandbox = daytona.create(sandbox_image(daytona), name=f"charts-{RUN}", wait_seconds=200)
    info(f"created sandbox id={sandbox.id} state={sandbox.state}")

    try:
        ok = require_started(sandbox) and read_charts(sandbox)
    finally:
        section("cleanup")
        daytona.delete(sandbox)
        info(f"deleted sandbox {sandbox.id}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
