// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Writes one eval's outcome (from the evals-action outputs in env) to a JSON file for
// the report job. The run response omits the report narrative, so fetch the key
// finding and friction points from the full report.

const fs = require('fs')
const path = require('path')

const API_ORIGIN = 'https://firegoat.ai'
const { redact } = require('./redact.js')

function truncate(text, max) {
  const value = String(text || '').trim()
  return value.length > max ? `${value.slice(0, max - 1)}…` : value
}

async function fetchReport(apiUrl, apiKey) {
  if (!apiUrl || !apiKey) return null
  try {
    const url = new URL(apiUrl)
    if (url.origin !== API_ORIGIN) return null
    const response = await fetch(url, {
      headers: { Authorization: `Bearer ${apiKey}` },
      signal: AbortSignal.timeout(15_000),
    })
    return response.ok ? await response.json() : null
  } catch {
    return null
  }
}

function frictionOf(full) {
  if (!full) return []
  if (Array.isArray(full.frictionPoints) && full.frictionPoints.length > 0) return full.frictionPoints
  return (full.legacy && Array.isArray(full.legacy.frictionPoints) && full.legacy.frictionPoints) || []
}

async function main(outFile) {
  const env = process.env
  let report = null
  try {
    report = env.REPORT_JSON ? JSON.parse(env.REPORT_JSON) : null
  } catch {
    report = null
  }
  const full = await fetchReport(report && report.apiUrl, env.EVALS_API_KEY)
  const result = {
    name: env.NAME,
    runId: env.RUN_ID,
    runUrl: env.RUN_ID ? `${API_ORIGIN}/evals/daytona.io/runs/${env.RUN_ID}` : '',
    finalStatus: env.FINAL_STATUS,
    outcome: env.OUTCOME,
    reportUrl: env.REPORT_URL,
    reportJson: env.REPORT_JSON,
    failureReason: env.FAILURE_REASON,
    // Firegoat moved the narrative under `legacy`; read the top level first.
    keyFinding: (full && (full.keyFinding || (full.legacy && full.legacy.keyFinding))) || '',
    frictionPoints: frictionOf(full).slice(0, 3).map((point) => ({
      severity: point.severity,
      text: truncate(redact(point.desc || point.title), 240),
    })),
  }
  fs.mkdirSync(path.dirname(outFile), { recursive: true })
  // The result is uploaded as an artifact and rendered in the PR comment.
  fs.writeFileSync(outFile, redact(JSON.stringify(result)))
}

main(process.argv[2])
