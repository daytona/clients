/*
 * Copyright Daytona Platforms Inc.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Charts: the data of a matplotlib figure a Python interpreter run displayed.
 *
 * A display that shows a matplotlib figure carries the figure's chart next to
 * its image: `InterpreterOutput.chart` (with the image in `.png`), and every
 * chart of a run in `InterpreterResult.charts`. `type` tells the charts
 * apart; a figure the sandbox cannot read as a chart, or a type this SDK
 * release does not know, is an {@link UnknownChart}.
 *
 * Values are as plotted: a number, or a string on a `categorical` axis (the
 * category) or a `datetime` axis (ISO 8601 in UTC, e.g.
 * `2024-01-01T00:00:00+00:00`). Long series are thinned, keeping each
 * stretch's lowest and highest point, and the chart says so with `truncated`.
 */

/** @internal The media type the chart travels under in a display bundle. */
export const CHART_MEDIA_TYPE = 'application/vnd.daytona.chart.v1+json'

/** A plotted value: a number, or a category or ISO 8601 UTC time as a string. */
export type ChartValue = number | string

/** An axis scale; open: a later sandbox may report others. */
export type ChartScale = 'linear' | 'log' | 'symlog' | 'logit' | 'asinh' | 'datetime' | 'categorical' | (string & {})

/** Which way bars or boxes run (`vertical`: categories along x); open. */
export type ChartOrientation = 'vertical' | 'horizontal' | (string & {})

export type ChartType = Chart['type']

/** One line or point series. */
export interface ChartSeries {
  /** `[x, y]` pairs in drawing order. */
  readonly points: readonly (readonly [ChartValue, ChartValue])[]
  /** The legend label; absent for an unlabelled series. */
  readonly label?: string
}

/** One bar. */
export interface ChartBar {
  /** The category the bar stands for: its tick label, else its position. */
  readonly label: string
  /** The bar's height (its width, for a horizontal bar). */
  readonly value: number
  /** The series' legend label; absent for an unlabelled series. */
  readonly group?: string
}

/** One pie slice. */
export interface ChartPieSlice {
  /** The slice's angle in degrees. */
  readonly angle: number
  readonly radius: number
  readonly label?: string
  /** The value the slice was drawn from. */
  readonly value?: number
  /** The percentage text drawn on the slice. */
  readonly autopct?: string
}

/** One box of a box-and-whisker chart; a statistic of an empty box is absent. */
export interface ChartBox {
  readonly label: string
  /** Values beyond the whiskers. */
  readonly outliers: readonly number[]
  /** The lower whisker. */
  readonly min?: number
  readonly firstQuartile?: number
  readonly median?: number
  readonly thirdQuartile?: number
  /** The upper whisker. */
  readonly max?: number
}

interface PointChart {
  readonly xScale: ChartScale
  readonly yScale: ChartScale
  /** Tick positions, as plotted values. */
  readonly xTicks: readonly ChartValue[]
  /** The text of each tick, as drawn. */
  readonly xTickLabels: readonly string[]
  readonly yTicks: readonly ChartValue[]
  readonly yTickLabels: readonly string[]
  readonly elements: readonly ChartSeries[]
  readonly title?: string
  readonly xLabel?: string
  readonly yLabel?: string
  /** Some series were thinned or left out. */
  readonly truncated: boolean
}

/** Line series (`plot`, pandas `plot()`, seaborn `lineplot`). */
export interface LineChart extends PointChart {
  readonly type: 'line'
}

/** Point series (`scatter`). */
export interface ScatterChart extends PointChart {
  readonly type: 'scatter'
}

/** Bars (`bar`, `barh`, `hist`, pandas `plot.bar()`). */
export interface BarChart {
  readonly type: 'bar'
  readonly orientation: ChartOrientation
  /** One element per bar, series by series. */
  readonly elements: readonly ChartBar[]
  readonly title?: string
  readonly xLabel?: string
  readonly yLabel?: string
  readonly truncated: boolean
}

/** Pie slices (`pie`). */
export interface PieChart {
  readonly type: 'pie'
  readonly elements: readonly ChartPieSlice[]
  readonly title?: string
  readonly truncated: boolean
}

/** Box statistics (`boxplot`, pandas `boxplot()`, seaborn `boxplot`). */
export interface BoxAndWhiskerChart {
  readonly type: 'box_and_whisker'
  readonly orientation: ChartOrientation
  readonly elements: readonly ChartBox[]
  readonly title?: string
  readonly xLabel?: string
  readonly yLabel?: string
  readonly truncated: boolean
}

/** A figure with several charts (subplots): one chart per axes, in order. */
export interface CompositeChart {
  readonly type: 'composite_chart'
  readonly elements: readonly Chart[]
  /** The figure's title (`suptitle`). */
  readonly title?: string
  readonly truncated: boolean
}

/**
 * A figure the sandbox cannot read as a chart, or a chart type this SDK
 * release does not know (`rawType` is the type the sandbox reported). Handle
 * it as "some other chart" rather than as an error.
 */
export interface UnknownChart {
  readonly type: 'unknown'
  readonly rawType: string
  readonly title?: string
  readonly truncated: boolean
}

export type Chart = LineChart | ScatterChart | BarChart | PieChart | BoxAndWhiskerChart | CompositeChart | UnknownChart

// The sandbox never nests composites; a payload nested deeper than this
// (which user code could publish) is malformed rather than walked.
const MAX_DEPTH = 3

class Malformed extends Error {}

/**
 * @internal The chart a display bundle carries, or `undefined` when its value
 * does not match the chart schema. The value is sandbox output, which user
 * code can publish too, so a malformed chart is no chart, never an error.
 */
export function chartFromWire(value: unknown): Chart | undefined {
  try {
    return chart(value, 0)
  } catch (error) {
    if (error instanceof Malformed) return undefined
    throw error
  }
}

function chart(value: unknown, depth: number): Chart {
  if (depth > MAX_DEPTH) throw new Malformed()
  const wire = object(value)
  const kind = wire.type
  if (typeof kind !== 'string') throw new Malformed()
  const title = optionalText(wire, 'title')
  const common = { ...(title === undefined ? {} : { title }), truncated: flag(wire, 'truncated') }
  switch (kind) {
    case 'line':
    case 'scatter':
      return {
        type: kind,
        xScale: text(wire, 'xScale'),
        yScale: text(wire, 'yScale'),
        xTicks: array(wire, 'xTicks').map(plotted),
        xTickLabels: array(wire, 'xTickLabels').map(string),
        yTicks: array(wire, 'yTicks').map(plotted),
        yTickLabels: array(wire, 'yTickLabels').map(string),
        elements: array(wire, 'elements').map(series),
        ...axisLabels(wire),
        ...common,
      }
    case 'bar':
      return { type: kind, orientation: text(wire, 'orientation'), elements: array(wire, 'elements').map(bar), ...axisLabels(wire), ...common }
    case 'pie':
      return { type: kind, elements: array(wire, 'elements').map(slice), ...common }
    case 'box_and_whisker':
      return { type: kind, orientation: text(wire, 'orientation'), elements: array(wire, 'elements').map(box), ...axisLabels(wire), ...common }
    case 'composite_chart':
      return { type: kind, elements: array(wire, 'elements').map((item) => chart(item, depth + 1)), ...common }
    default:
      return { type: 'unknown', rawType: kind, ...common }
  }
}

function axisLabels(wire: Record<string, unknown>): { readonly xLabel?: string; readonly yLabel?: string } {
  const xLabel = optionalText(wire, 'xLabel')
  const yLabel = optionalText(wire, 'yLabel')
  return { ...(xLabel === undefined ? {} : { xLabel }), ...(yLabel === undefined ? {} : { yLabel }) }
}

function series(value: unknown): ChartSeries {
  const wire = object(value)
  const points = array(wire, 'points').map((item): readonly [ChartValue, ChartValue] => {
    if (!Array.isArray(item) || item.length !== 2) throw new Malformed()
    return [plotted(item[0]), plotted(item[1])]
  })
  const label = optionalText(wire, 'label')
  return { points, ...(label === undefined ? {} : { label }) }
}

function bar(value: unknown): ChartBar {
  const wire = object(value)
  const group = optionalText(wire, 'group')
  return { label: text(wire, 'label'), value: number(wire, 'value'), ...(group === undefined ? {} : { group }) }
}

function slice(value: unknown): ChartPieSlice {
  const wire = object(value)
  const label = optionalText(wire, 'label')
  const sliceValue = optionalNumber(wire, 'value')
  const autopct = optionalText(wire, 'autopct')
  return {
    angle: number(wire, 'angle'),
    radius: number(wire, 'radius'),
    ...(label === undefined ? {} : { label }),
    ...(sliceValue === undefined ? {} : { value: sliceValue }),
    ...(autopct === undefined ? {} : { autopct }),
  }
}

const BOX_STATISTICS = ['min', 'firstQuartile', 'median', 'thirdQuartile', 'max'] as const

function box(value: unknown): ChartBox {
  const wire = object(value)
  const statistics: { -readonly [K in (typeof BOX_STATISTICS)[number]]?: number } = {}
  for (const key of BOX_STATISTICS) {
    const statistic = optionalNumber(wire, key)
    if (statistic !== undefined) statistics[key] = statistic
  }
  return { label: text(wire, 'label'), outliers: array(wire, 'outliers').map(finite), ...statistics }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function object(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) throw new Malformed()
  return value
}

function array(wire: Record<string, unknown>, key: string): unknown[] {
  const value = wire[key]
  if (!Array.isArray(value)) throw new Malformed()
  return value
}

function text(wire: Record<string, unknown>, key: string): string {
  return string(wire[key])
}

function string(value: unknown): string {
  if (typeof value !== 'string') throw new Malformed()
  return value
}

function optionalText(wire: Record<string, unknown>, key: string): string | undefined {
  const value = wire[key]
  return value === undefined || value === null ? undefined : string(value)
}

function flag(wire: Record<string, unknown>, key: string): boolean {
  const value = wire[key] ?? false
  if (typeof value !== 'boolean') throw new Malformed()
  return value
}

function finite(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Malformed()
  return value
}

function number(wire: Record<string, unknown>, key: string): number {
  return finite(wire[key])
}

function optionalNumber(wire: Record<string, unknown>, key: string): number | undefined {
  const value = wire[key]
  return value === undefined || value === null ? undefined : finite(value)
}

function plotted(value: unknown): ChartValue {
  return typeof value === 'string' ? value : finite(value)
}
