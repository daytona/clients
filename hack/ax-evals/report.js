// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Merges the per-eval result files (<eval>.json, downloaded into one directory) written
// by the eval matrix into one sticky PR comment and the job summary.

const fs = require('fs')
const path = require('path')
const { upsertComment } = require('./comment.js')

function parseReport(raw) {
  try {
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function checklistOf(report) {
  return (report && report.reward && report.reward.checklist) || {}
}

function failedChecks(report) {
  return Object.entries(checklistOf(report))
    .filter(([, score]) => score !== 1)
    .map(([slug]) => slug)
}

function isClean(result) {
  const report = parseReport(result.reportJson)
  return (
    result.finalStatus === 'completed' &&
    result.outcome === 'succeeded' &&
    Object.keys(checklistOf(report)).length > 0 &&
    failedChecks(report).length === 0
  )
}

function resultLabel(result) {
  if (result.finalStatus === 'completed' && result.outcome === 'succeeded') {
    if (isClean(result)) return '✅ Succeeded'
    return Object.keys(checklistOf(parseReport(result.reportJson))).length > 0
      ? '⚠️ Succeeded, checks failed'
      : '⚠️ Succeeded, checks missing'
  }
  if (result.finalStatus === 'completed') return `❌ ${result.outcome || 'no report'}`
  if (result.finalStatus === 'failed') return '💥 Failed'
  if (result.finalStatus === 'superseded') return '↪️ Superseded'
  if (result.finalStatus === 'running') return '⏱️ Timed out'
  return '⚠️ Not started'
}

function checksCell(report) {
  const checklist = report && report.reward && report.reward.checklist
  if (!checklist) return '–'
  const total = Object.keys(checklist).length
  const failed = failedChecks(report)
  const summary = `${total - failed.length}/${total}`
  return failed.length === 0 ? summary : `${summary} (failed: ${failed.map((slug) => `\`${slug}\``).join(', ')})`
}

function oneLine(text) {
  return (
    String(text || '')
      .replace(/\s+/g, ' ')
      .replace(/\\/g, '\\\\')
      // Agent-written text must not notify users or teams.
      .replace(/@(?=[\w-])/g, '@\u200b')
      .replace(/\|/g, '\\|')
      .trim()
  )
}

function render({ results, sha, runUrl }) {
  const rows = results.map((result) => {
    const report = parseReport(result.reportJson)
    const metrics = (report && report.metrics) || {}
    const link = result.reportUrl ? `[report](${result.reportUrl})` : result.runUrl ? `[run](${result.runUrl})` : '–'
    return `| ${result.title} | ${resultLabel(result)} | ${checksCell(report)} | ${metrics.time || '–'} | ${metrics.cost || '–'} | ${metrics.errors ?? '–'} | ${link} |`
  })
  const details = results
    .map((result) => {
      const finding = result.keyFinding || result.failureReason
      if (!finding) return null
      const frictions = (result.frictionPoints || []).map((point) => `  - _${point.severity}_: ${oneLine(point.text)}`)
      return [`- **${result.title}:** ${oneLine(finding)}`, ...frictions].join('\n')
    })
    .filter(Boolean)
  const allSucceeded = results.every(isClean)
  return [
    '### AX evals (2027.dev)',
    '',
    `${allSucceeded ? '✅ All evals succeeded' : 'Some evals did not succeed'} on commit \`${sha.slice(0, 7)}\`. Informational only, this never blocks merging.`,
    '',
    '| Eval | Result | Checks | Agent time | Cost | Errors | Details |',
    '| --- | --- | --- | --- | --- | --- | --- |',
    ...rows,
    ...(details.length
      ? ['', '<details><summary>Key findings and friction points</summary>', '', ...details, '', '</details>']
      : []),
    '',
    `Re-run with a \`/ax-eval\` comment (\`/ax-eval all\` or e.g. \`/ax-eval python go\`) · [workflow run](${runUrl})`,
  ]
}

function loadResults(dir, matrix) {
  return matrix.map((entry) => {
    const file = path.join(dir, `${entry.name}.json`)
    if (!fs.existsSync(file)) return { name: entry.name, title: entry.title, finalStatus: '' }
    return { ...JSON.parse(fs.readFileSync(file, 'utf8')), title: entry.title }
  })
}

async function run({ github, context, core }) {
  const matrix = JSON.parse(process.env.AX_MATRIX)
  const results = loadResults(process.env.AX_RESULTS_DIR, matrix)
  const runUrl = `${context.serverUrl}/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId}`
  const lines = render({ results, sha: process.env.AX_SHA, runUrl })
  await core.summary.addRaw(lines.join('\n')).write()
  const written = await upsertComment(github, context.repo, Number(process.env.AX_PR), context.runId, lines)
  if (!written) core.notice('A newer AX eval run already owns the PR comment; leaving it untouched')
}

module.exports = run
module.exports.render = render
module.exports.loadResults = loadResults
