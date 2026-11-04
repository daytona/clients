// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Decides whether and what to evaluate for an `ax-eval` label or a manual workflow run.
// Sets outputs: matrix (JSON list of {name, title, promptId}), sha, pr.

const fs = require('fs')
const path = require('path')
const { MARKER, upsertComment } = require('./comment.js')

const ALIASES = { ts: 'typescript', js: 'typescript', py: 'python', golang: 'go' }
const WRITE_ROLES = new Set(['admin', 'maintain', 'write'])
// The prompts and evals.json paths describe the 0.x layout; main (2.x) differs.
const BASE_BRANCH = 'release/v0'
const DISPATCH_HINT = `gh workflow run ax_evals.yaml --repo daytona/clients --ref ${BASE_BRANCH} -f pr=<number> -f evals=all`

function parseEvals(input) {
  return (input || '')
    .split(/[\s,]+/)
    .filter(Boolean)
    .map((token) => token.toLowerCase())
}

function selectByPaths(config, files) {
  const touches = (prefixes) => files.some((file) => prefixes.some((prefix) => file.startsWith(prefix)))
  if (touches(config.sharedPaths)) return Object.keys(config.evals)
  return Object.entries(config.evals)
    .filter(([, spec]) => touches(spec.paths))
    .map(([name]) => name)
}

async function run({ github, context, core }) {
  const config = JSON.parse(fs.readFileSync(path.join(__dirname, 'evals.json'), 'utf8'))
  const repo = context.repo
  const isDispatch = context.eventName === 'workflow_dispatch'
  const prNumber = isDispatch ? Number(context.payload.inputs.pr) : context.payload.pull_request.number
  const actor = context.payload.sender.login
  core.setOutput('matrix', '[]')
  if (!Number.isInteger(prNumber) || prNumber <= 0) {
    return core.setFailed(`Not a pull request number: ${context.payload.inputs?.pr}`)
  }
  const requested = isDispatch ? parseEvals(context.payload.inputs.evals) : []

  let role = 'none'
  try {
    const { data: permission } = await github.rest.repos.getCollaboratorPermissionLevel({ ...repo, username: actor })
    role = permission.role_name
  } catch (error) {
    // 404 for users who are not collaborators at all.
    core.info(`Could not read @${actor}'s permission: ${error.message}`)
  }
  if (!WRITE_ROLES.has(role)) {
    return core.notice(`@${actor} has '${role}' access; AX evals need write access`)
  }

  const { data: pr } = await github.rest.pulls.get({ ...repo, pull_number: prNumber })
  if (pr.state !== 'open') return core.notice(`PR #${prNumber} is ${pr.state}`)
  if (pr.base.ref !== BASE_BRANCH) {
    return core.notice(`PR #${prNumber} targets ${pr.base.ref}; AX evals only cover PRs against ${BASE_BRANCH}`)
  }
  if (!pr.head.repo || pr.head.repo.full_name !== `${repo.owner}/${repo.repo}`) {
    return core.notice('AX evals are not supported on pull requests from forks')
  }

  const names = Object.keys(config.evals)
  let selected
  if (requested.includes('all')) {
    selected = names
  } else if (requested.length > 0) {
    const resolved = requested.map((token) => ALIASES[token] || token)
    const unknown = resolved.filter((name) => !names.includes(name))
    if (unknown.length > 0) {
      return core.setFailed(`Unknown eval(s): ${unknown.join(', ')}. Valid: all, ${names.join(', ')}`)
    }
    selected = [...new Set(resolved)]
  } else {
    const files = await github.paginate(github.rest.pulls.listFiles, { ...repo, pull_number: prNumber, per_page: 100 })
    selected = selectByPaths(
      config,
      files.flatMap((file) => [file.filename, file.previous_filename].filter(Boolean)),
    )
  }

  const sha = pr.head.sha
  const runUrl = `${context.serverUrl}/${repo.owner}/${repo.repo}/actions/runs/${context.runId}`
  if (selected.length === 0) {
    await upsertComment(github, repo, prNumber, context.runId, [
      '### AX evals (2027.dev)',
      '',
      `This PR doesn't change any SDK or CLI sources (commit \`${sha.slice(0, 7)}\`), so nothing was evaluated.`,
      `To run evals anyway: \`${DISPATCH_HINT.replace('<number>', prNumber)}\` (replace \`all\` with e.g. \`'python go'\` for a subset).`,
    ])
    return
  }

  const matrix = selected.map((name) => ({
    name,
    title: config.evals[name].name,
    promptId: config.evals[name].promptId,
  }))
  core.setOutput('matrix', JSON.stringify(matrix))
  core.setOutput('sha', sha)
  core.setOutput('pr', String(prNumber))
  await upsertComment(github, repo, prNumber, context.runId, [
    '### AX evals (2027.dev)',
    '',
    `⏳ Running ${matrix.map((m) => m.title).join(', ')} on commit \`${sha.slice(0, 7)}\`. Results usually take 7–10 minutes, longer when the 2027 queue is busy ([workflow run](${runUrl})).`,
  ])
}

module.exports = run
module.exports.parseEvals = parseEvals
module.exports.selectByPaths = selectByPaths
module.exports.MARKER = MARKER
module.exports.DISPATCH_HINT = DISPATCH_HINT
