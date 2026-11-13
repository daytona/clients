// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Writes one eval's outcome (from the evals-action outputs in env) to a JSON file for
// the report job. The report the action returns carries the narrative: Firegoat puts
// the key finding and friction points at its top level or under `legacy`.

const fs = require('fs')
const path = require('path')
const { redact } = require('./redact.js')

const API_ORIGIN = 'https://firegoat.ai'

function truncate(text, max) {
  const value = String(text || '').trim()
  return value.length > max ? `${value.slice(0, max - 1)}…` : value
}

function keyFindingOf(report) {
  if (!report) return ''
  return report.keyFinding || (report.legacy && report.legacy.keyFinding) || ''
}

function frictionOf(report) {
  if (!report) return []
  if (Array.isArray(report.frictionPoints) && report.frictionPoints.length > 0) return report.frictionPoints
  return (report.legacy && Array.isArray(report.legacy.frictionPoints) && report.legacy.frictionPoints) || []
}

function main(outFile) {
  const env = process.env
  let report = null
  try {
    report = env.REPORT_JSON ? JSON.parse(env.REPORT_JSON) : null
  } catch {
    report = null
  }
  const result = {
    name: env.NAME,
    runId: env.RUN_ID,
    runUrl: env.RUN_ID ? `${API_ORIGIN}/evals/daytona.io/runs/${env.RUN_ID}` : '',
    finalStatus: env.FINAL_STATUS,
    outcome: env.OUTCOME,
    reportUrl: env.REPORT_URL,
    reportJson: env.REPORT_JSON,
    failureReason: env.FAILURE_REASON,
    keyFinding: truncate(redact(keyFindingOf(report)), 600),
    frictionPoints: frictionOf(report).slice(0, 3).map((point) => ({
      severity: point.severity,
      text: truncate(redact(point.desc || point.title), 240),
    })),
  }
  fs.mkdirSync(path.dirname(outFile), { recursive: true })
  // The result is uploaded as an artifact and rendered in the PR comment.
  fs.writeFileSync(outFile, redact(JSON.stringify(result)))
}

main(process.argv[2])
