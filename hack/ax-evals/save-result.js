// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Writes one eval's outcome (from the evals-action outputs in env) to a JSON file for
// the report job. The run response omits the report narrative, so fetch the key
// finding and friction points from the full report.

const fs = require('fs')
const path = require('path')

const API_ORIGIN = 'https://2027.dev'

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
    runUrl: env.RUN_ID ? `https://2027.dev/evals/daytona.io/runs/${env.RUN_ID}` : '',
    finalStatus: env.FINAL_STATUS,
    outcome: env.OUTCOME,
    reportUrl: env.REPORT_URL,
    reportJson: env.REPORT_JSON,
    failureReason: env.FAILURE_REASON,
    keyFinding: (full && full.keyFinding) || '',
    frictionPoints: ((full && full.frictionPoints) || []).slice(0, 3).map((point) => ({
      severity: point.severity,
      text: truncate(point.desc || point.title, 240),
    })),
  }
  fs.mkdirSync(path.dirname(outFile), { recursive: true })
  fs.writeFileSync(outFile, JSON.stringify(result))
}

main(process.argv[2])
