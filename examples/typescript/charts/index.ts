/*
 * charts — read a matplotlib figure's data, not just its image.
 *
 * Installs matplotlib in the sandbox, draws a figure with two subplots in the
 * interpreter, and reads each subplot's chart from the run's result.
 *
 * Run (from examples):
 *     DAYTONA_API_KEY=dtn_... \
 *       npx tsx typescript/charts/index.ts
 */

import type { Chart, Sandbox } from '@daytona/sdk/v2'
import { assertStarted, client, section, info, fail, RUN, sandboxImage } from '../_common'

const FIGURE = `
import matplotlib.pyplot as plt
fig, (left, right) = plt.subplots(1, 2, figsize=(8, 3))
left.plot([1, 2, 3, 4], [10, 20, 15, 30], label="visits")
left.set_title("traffic")
right.bar(["a", "b", "c"], [3, 7, 5])
right.set_title("signups")
plt.show()
`

/** One line about a chart: its type, title and data. */
function describe(chart: Chart): string {
  switch (chart.type) {
    case 'line':
      return `line '${chart.title}' (${chart.elements.map((series) => `${series.label ?? 'series'}: ${series.points.length} points`).join(', ')})`
    case 'bar':
      return `bar '${chart.title}' (${chart.elements.map((bar) => `${bar.label}=${bar.value}`).join(', ')})`
    default:
      return `${chart.type} '${chart.title}'`
  }
}

/** Draw the figure; resolve to whether its chart and image came back. */
async function readCharts(sandbox: Sandbox): Promise<boolean> {
  section('install matplotlib')
  const install = await sandbox.process.run('python3 -m pip install --quiet matplotlib')
  if (install.exitCode !== 0) {
    fail(`pip install failed (exit=${install.exitCode}): ${install.output.trim().slice(-300)}`)
    return false
  }

  section('draw a figure and read its chart')
  const result = await sandbox.interpreter.run(FIGURE, { language: 'python' })
  if (result.error) {
    fail(`the figure raised ${result.error.name}: ${result.error.value}`)
    return false
  }
  // Every figure the run displayed; prefer it to result.results, where a
  // pandas or seaborn call puts its axes first.
  const charts = result.charts ?? []
  const figure = charts[0]
  if (charts.length !== 1 || figure?.type !== 'composite_chart') {
    fail(`expected one figure with subplots, got ${JSON.stringify(charts.map((chart) => chart.type))}`)
    return false
  }
  for (const subplot of figure.elements) info(describe(subplot))
  const kinds = figure.elements.map((subplot) => subplot.type)
  if (kinds.join() !== 'line,bar') {
    fail(`expected a line and a bar subplot, got ${JSON.stringify(kinds)}`)
    return false
  }
  // The figure's image is in the same output as its chart.
  const image = result.results.find((output) => output.chart === figure)?.png
  if (!image) {
    fail("the figure's image is missing")
    return false
  }
  info(`image: ${image.length} base64 characters of PNG`)
  return true
}

async function main() {
  const daytona = client()

  const sandbox = await daytona.create(await sandboxImage(daytona), { name: `charts-${RUN}`, waitSeconds: 200 })
  info(`created sandbox id=${sandbox.id} state=${sandbox.state}`)

  let ok = false
  try {
    assertStarted(sandbox)
    ok = await readCharts(sandbox)
  } finally {
    section('cleanup')
    await daytona.delete(sandbox)
    info(`deleted sandbox ${sandbox.id}`)
  }

  process.exit(ok ? 0 : 1)
}

main().catch((e) => {
  console.error('Error:', e)
  process.exit(1)
})
