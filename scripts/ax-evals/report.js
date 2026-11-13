// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Merges the per-eval result files (<eval>.json, downloaded into one directory) written
// by the eval matrix into one sticky PR comment and the job summary.

const fs = require('fs')
const path = require('path')
const { upsertComment } = require('./comment.js')
const { redact } = require('./redact.js')
const { DISPATCH_HINT, HEADING } = require('./plan.js')

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
  if (result.finalStatus === 'completed') return `❌ ${oneLine(result.outcome || 'no report', MAX_CELL)}`
  if (result.finalStatus === 'failed') return '💥 Failed'
  if (result.finalStatus === 'superseded') return '↪️ Superseded'
  if (result.finalStatus === 'running') return '⏱️ Timed out'
  if (!result.runId && result.failureReason) return '💥 Could not start'
  // Started on Firegoat, but the job lost track of it (stopped, timed out or a polling error).
  if (result.runId) return '⏹️ Did not finish'
  return '⚠️ Not started'
}

function checksCell(report) {
  const total = Object.keys(checklistOf(report)).length
  if (total === 0) return '–'
  const failed = failedChecks(report)
  const summary = `${total - failed.length}/${total}`
  if (failed.length === 0) return summary
  const named = failed.slice(0, 3).map((slug) => oneLine(slug, MAX_CELL))
  const more = failed.length > named.length ? `, +${failed.length - named.length} more` : ''
  return `${summary} (failed: ${named.join(', ')}${more})`
}

// Agent and judge text is capped before escaping; table cells far more tightly.
const MAX_TEXT = 600
const MAX_CELL = 40
// GitHub refuses a comment over 65,536 characters; keep a margin.
const MAX_COMMENT = 65000

function oneLine(text, max = MAX_TEXT) {
  const flat = redact(text).replace(/\s+/g, ' ').trim()
  return (
    (flat.length > max ? `${flat.slice(0, max - 1)}…` : flat)
      // Agent-written text is shown as plain text: no Markdown links, images, HTML, math,
      // character references (`&commat;` would become a mention) or formatting of its
      // own, and no table cell breaks.
      .replace(/[&\\`*_{}\[\]()<>#+!|~]/g, '\\$&')
      // `$…$` would render as math, where the escapes above are undone. With two or
      // more dollars, each goes inside a tag, which is plain text. (A function: a `$<`
      // replacement string is a group reference.)
      .replace(/\$/g, (dollar, offset, all) => (all.indexOf('$') === all.lastIndexOf('$') ? dollar : '<span>$</span>'))
      // Nor may it notify users or teams, cross-reference issues, or autolink URLs.
      .replace(/@(?=[\w-])/g, '@\u200b')
      .replace(/#(?=\w)/g, '#\u200b')
      .replace(/\bGH-(?=\d)/gi, (m) => `${m.slice(0, 2)}\u200b-`)
      .replace(/:\/\//g, ':\u200b//')
      .replace(/\bwww\./gi, (m) => `${m.slice(0, 3)}\u200b.`)
  )
}

// Links in the comment only ever point at Firegoat; anything else is dropped.
function firegoatUrl(value) {
  try {
    const url = new URL(String(value || ''))
    return url.origin === 'https://firegoat.ai' ? url.href.replace(/\(/g, '%28').replace(/\)/g, '%29') : ''
  } catch {
    return ''
  }
}

function render({ results, sha, runUrl, pr }) {
  const rows = results.map((result) => {
    const report = parseReport(result.reportJson)
    const metrics = (report && report.metrics) || {}
    const reportUrl = firegoatUrl(result.reportUrl)
    const runUrl = firegoatUrl(result.runUrl)
    const link = reportUrl ? `[report](${reportUrl})` : runUrl ? `[run](${runUrl})` : '–'
    const cell = (value) => (value === undefined || value === null || value === '' ? '–' : oneLine(value, MAX_CELL))
    return `| ${result.title} | ${resultLabel(result)} | ${checksCell(report)} | ${cell(metrics.time)} | ${cell(metrics.cost)} | ${cell(metrics.errors)} | ${link} |`
  })
  const details = results
    .map((result) => {
      const finding = result.keyFinding || result.failureReason || (parseReport(result.reportJson) || {}).nullNotice
      const frictions = (result.frictionPoints || []).map((point) => `  - _${point.severity}_: ${oneLine(point.text)}`)
      if (!finding && frictions.length === 0) return null
      return [`- **${result.title}:** ${finding ? oneLine(finding) : 'friction points'}`, ...frictions].join('\n')
    })
    .filter(Boolean)
  const allSucceeded = results.every(isClean)
  const lines = (shown) => [
    HEADING,
    '',
    `${allSucceeded ? '✅ All evals succeeded' : 'Some evals did not succeed'} on commit \`${sha.slice(0, 7)}\`. Informational only, this never blocks merging.`,
    '',
    '| Eval | Result | Checks | Agent time | Cost | Errors | Details |',
    '| --- | --- | --- | --- | --- | --- | --- |',
    ...rows,
    ...(shown.length
      ? ['', '<details><summary>Key findings and friction points</summary>', '', ...shown, '', '</details>']
      : []),
    '',
    `Re-run: remove and re-add the \`ax-eval\` label, or \`${DISPATCH_HINT.replace('<number>', pr || '<number>')}\` · [workflow run](${runUrl})`,
  ]
  // Escaping can grow text well past its cap (a `$` becomes 14 characters): drop
  // findings from the end until the whole comment fits GitHub's limit.
  let shown = details
  while (shown.length > 0 && lines(shown).join('\n').length > MAX_COMMENT) shown = shown.slice(0, -1)
  return lines(shown)
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
  const lines = render({ results, sha: process.env.AX_SHA, runUrl, pr: process.env.AX_PR })
  await core.summary.addRaw(lines.join('\n')).write()
  const written = await upsertComment(github, context.repo, Number(process.env.AX_PR), context.runId, lines)
  if (!written) core.notice('A newer AX eval run already owns the PR comment; leaving it untouched')
}

module.exports = run
module.exports.render = render
module.exports.loadResults = loadResults
