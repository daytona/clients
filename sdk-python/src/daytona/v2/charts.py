# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Charts: the data of a matplotlib figure a Python interpreter run displayed.

A display that shows a matplotlib figure carries the figure's chart next to
its image: ``InterpreterOutput.chart`` (with the image in ``.png``), and every
chart of a run in ``InterpreterResult.charts``. ``type`` tells the chart
classes apart; a figure the sandbox cannot read as a chart, or a type this SDK
release does not know, is an :class:`UnknownChart`.

Values are as plotted: a number, or a string on a ``categorical`` axis (the
category) or a ``datetime`` axis (ISO 8601 in UTC, e.g.
``2024-01-01T00:00:00+00:00``). Long series are thinned, keeping each
stretch's lowest and highest point, and the chart says so with ``truncated``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Mapping, Optional, Union

_CHART_MEDIA_TYPE = "application/vnd.daytona.chart.v1+json"

ChartValue = Union[float, str]
"""A plotted value: a number, or a category or ISO 8601 UTC time as a string."""

ChartScale = Union[Literal["linear", "log", "symlog", "logit", "asinh", "datetime", "categorical"], str]
"""An axis scale; open: a later sandbox may report others."""

ChartOrientation = Union[Literal["vertical", "horizontal"], str]
"""Which way bars or boxes run (``vertical``: categories along x); open."""

ChartType = Literal["line", "scatter", "bar", "pie", "box_and_whisker", "composite_chart", "unknown"]


@dataclass(frozen=True, slots=True, kw_only=True)
class ChartSeries:
    """One line or point series."""

    points: tuple[tuple[ChartValue, ChartValue], ...]
    """``(x, y)`` pairs in drawing order."""
    label: Optional[str] = None
    """The legend label; ``None`` for an unlabelled series."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ChartBar:
    """One bar."""

    label: str
    """The category the bar stands for: its tick label, else its position."""
    value: float
    """The bar's height (its width, for a horizontal bar)."""
    group: Optional[str] = None
    """The series' legend label; ``None`` for an unlabelled series."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ChartPieSlice:
    """One pie slice."""

    angle: float
    """The slice's angle in degrees."""
    radius: float
    label: Optional[str] = None
    value: Optional[float] = None
    """The value the slice was drawn from."""
    autopct: Optional[str] = None
    """The percentage text drawn on the slice."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ChartBox:
    """One box of a box-and-whisker chart; a statistic of an empty box is ``None``."""

    label: str
    outliers: tuple[float, ...] = ()
    """Values beyond the whiskers."""
    min: Optional[float] = None
    """The lower whisker."""
    first_quartile: Optional[float] = None
    median: Optional[float] = None
    third_quartile: Optional[float] = None
    max: Optional[float] = None
    """The upper whisker."""


@dataclass(frozen=True, slots=True, kw_only=True)
class _PointChart:
    x_scale: ChartScale
    y_scale: ChartScale
    x_ticks: tuple[ChartValue, ...] = ()
    """Tick positions, as plotted values."""
    x_tick_labels: tuple[str, ...] = ()
    """The text of each tick, as drawn."""
    y_ticks: tuple[ChartValue, ...] = ()
    y_tick_labels: tuple[str, ...] = ()
    elements: tuple[ChartSeries, ...] = ()
    title: Optional[str] = None
    x_label: Optional[str] = None
    y_label: Optional[str] = None
    truncated: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class LineChart(_PointChart):
    """Line series (``plot``, pandas ``plot()``, seaborn ``lineplot``)."""

    type: Literal["line"] = "line"


@dataclass(frozen=True, slots=True, kw_only=True)
class ScatterChart(_PointChart):
    """Point series (``scatter``)."""

    type: Literal["scatter"] = "scatter"


@dataclass(frozen=True, slots=True, kw_only=True)
class BarChart:
    """Bars (``bar``, ``barh``, ``hist``, pandas ``plot.bar()``)."""

    orientation: ChartOrientation
    elements: tuple[ChartBar, ...] = ()
    """One element per bar, series by series."""
    title: Optional[str] = None
    x_label: Optional[str] = None
    y_label: Optional[str] = None
    truncated: bool = False
    type: Literal["bar"] = "bar"


@dataclass(frozen=True, slots=True, kw_only=True)
class PieChart:
    """Pie slices (``pie``)."""

    elements: tuple[ChartPieSlice, ...] = ()
    title: Optional[str] = None
    truncated: bool = False
    type: Literal["pie"] = "pie"


@dataclass(frozen=True, slots=True, kw_only=True)
class BoxAndWhiskerChart:
    """Box statistics (``boxplot``, pandas ``boxplot()``, seaborn ``boxplot``)."""

    orientation: ChartOrientation
    elements: tuple[ChartBox, ...] = ()
    title: Optional[str] = None
    x_label: Optional[str] = None
    y_label: Optional[str] = None
    truncated: bool = False
    type: Literal["box_and_whisker"] = "box_and_whisker"


@dataclass(frozen=True, slots=True, kw_only=True)
class CompositeChart:
    """A figure with several charts (subplots): one chart per axes, in order."""

    elements: tuple[Chart, ...] = ()
    title: Optional[str] = None
    """The figure's title (``suptitle``)."""
    truncated: bool = False
    type: Literal["composite_chart"] = "composite_chart"


@dataclass(frozen=True, slots=True, kw_only=True)
class UnknownChart:
    """A figure the sandbox cannot read as a chart, or a chart type this SDK
    release does not know (``raw_type`` is the type the sandbox reported).
    Handle it as "some other chart" rather than as an error."""

    raw_type: str
    title: Optional[str] = None
    truncated: bool = False
    type: Literal["unknown"] = "unknown"


Chart = Union[LineChart, ScatterChart, BarChart, PieChart, BoxAndWhiskerChart, CompositeChart, UnknownChart]

# The sandbox never nests composites; a payload nested deeper than this
# (which user code could publish) is malformed rather than walked.
_MAX_DEPTH = 3


class _Malformed(Exception):
    pass


def _parse_chart(value: object) -> Optional[Chart]:
    """The chart a display bundle carries, or ``None`` when its value does not
    match the chart schema. The value is sandbox output, which user code can
    publish too, so a malformed chart is no chart, never an error."""
    try:
        return _chart(value, 0)
    except _Malformed:
        return None


def _chart(value: object, depth: int) -> Chart:
    if depth > _MAX_DEPTH:
        raise _Malformed
    wire = _object(value)
    kind = wire.get("type")
    if not isinstance(kind, str):
        raise _Malformed
    title = _optional_text(wire, "title")
    truncated = _flag(wire, "truncated")
    if kind in ("line", "scatter"):
        point_chart = LineChart if kind == "line" else ScatterChart
        return point_chart(
            x_scale=_text(wire, "xScale"),
            y_scale=_text(wire, "yScale"),
            x_ticks=tuple(_value(item) for item in _array(wire, "xTicks")),
            x_tick_labels=tuple(_string(item) for item in _array(wire, "xTickLabels")),
            y_ticks=tuple(_value(item) for item in _array(wire, "yTicks")),
            y_tick_labels=tuple(_string(item) for item in _array(wire, "yTickLabels")),
            elements=tuple(_series(item) for item in _array(wire, "elements")),
            title=title,
            x_label=_optional_text(wire, "xLabel"),
            y_label=_optional_text(wire, "yLabel"),
            truncated=truncated,
        )
    if kind == "bar":
        return BarChart(
            orientation=_text(wire, "orientation"),
            elements=tuple(_bar(item) for item in _array(wire, "elements")),
            title=title,
            x_label=_optional_text(wire, "xLabel"),
            y_label=_optional_text(wire, "yLabel"),
            truncated=truncated,
        )
    if kind == "pie":
        return PieChart(
            elements=tuple(_slice(item) for item in _array(wire, "elements")),
            title=title,
            truncated=truncated,
        )
    if kind == "box_and_whisker":
        return BoxAndWhiskerChart(
            orientation=_text(wire, "orientation"),
            elements=tuple(_box(item) for item in _array(wire, "elements")),
            title=title,
            x_label=_optional_text(wire, "xLabel"),
            y_label=_optional_text(wire, "yLabel"),
            truncated=truncated,
        )
    if kind == "composite_chart":
        return CompositeChart(
            elements=tuple(_chart(item, depth + 1) for item in _array(wire, "elements")),
            title=title,
            truncated=truncated,
        )
    return UnknownChart(raw_type=kind, title=title, truncated=truncated)


def _series(value: object) -> ChartSeries:
    wire = _object(value)
    points = []
    for item in _array(wire, "points"):
        if not isinstance(item, list) or len(item) != 2:
            raise _Malformed
        points.append((_value(item[0]), _value(item[1])))
    return ChartSeries(points=tuple(points), label=_optional_text(wire, "label"))


def _bar(value: object) -> ChartBar:
    wire = _object(value)
    return ChartBar(
        label=_text(wire, "label"), value=_number(wire, "value"), group=_optional_text(wire, "group")
    )


def _slice(value: object) -> ChartPieSlice:
    wire = _object(value)
    return ChartPieSlice(
        angle=_number(wire, "angle"),
        radius=_number(wire, "radius"),
        label=_optional_text(wire, "label"),
        value=_optional_number(wire, "value"),
        autopct=_optional_text(wire, "autopct"),
    )


def _box(value: object) -> ChartBox:
    wire = _object(value)
    return ChartBox(
        label=_text(wire, "label"),
        outliers=tuple(_finite(item) for item in _array(wire, "outliers")),
        min=_optional_number(wire, "min"),
        first_quartile=_optional_number(wire, "firstQuartile"),
        median=_optional_number(wire, "median"),
        third_quartile=_optional_number(wire, "thirdQuartile"),
        max=_optional_number(wire, "max"),
    )


def _object(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise _Malformed
    return value


def _array(wire: Mapping[str, object], key: str) -> list[object]:
    value = wire.get(key)
    if not isinstance(value, list):
        raise _Malformed
    return value


def _text(wire: Mapping[str, object], key: str) -> str:
    return _string(wire.get(key))


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise _Malformed
    return value


def _optional_text(wire: Mapping[str, object], key: str) -> Optional[str]:
    value = wire.get(key)
    return None if value is None else _string(value)


def _flag(wire: Mapping[str, object], key: str) -> bool:
    value = wire.get(key)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise _Malformed
    return value


def _finite(value: object) -> float:
    # A number a double carries: not a bool, finite, and an int no larger
    # than a float (JSON.parse reads a larger one as Infinity).
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _Malformed
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise _Malformed
    return value


def _number(wire: Mapping[str, object], key: str) -> float:
    return _finite(wire.get(key))


def _optional_number(wire: Mapping[str, object], key: str) -> Optional[float]:
    value = wire.get(key)
    return None if value is None else _finite(value)


def _value(value: object) -> ChartValue:
    return value if isinstance(value, str) else _finite(value)
