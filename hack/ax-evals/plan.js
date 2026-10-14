// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Decides whether and what to evaluate for an `ax-eval` label or `/ax-eval` comment.
// Sets outputs: matrix (JSON list of {name, title, promptId}), sha, pr.

const fs = require('fs')
const path = require('path')
const { MARKER, upsertComment } = require('./comment.js')

const ALIASES = { ts: 'typescript', js: 'typescript', py: 'python', golang: 'go' }
const WRITE_ROLES = new Set(['admin', 'maintain', 'write'])

function parseCommand(body) {
  const firstLine = (body || '').trim().split('\n')[0].trim()
  const match = /^\/ax-eval(?:\s+(.*))?$/i.exec(firstLine)
  if (!match) return null
  return (match[1] || '')
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
  const isComment = context.eventName === 'issue_comment'
  const prNumber = isComment ? context.payload.issue.number : context.payload.pull_request.number
  const actor = context.payload.sender.login
  core.setOutput('matrix', '[]')

  const react = async (content) => {
    if (!isComment) return
    await github.rest.reactions.createForIssueComment({ ...repo, comment_id: context.payload.comment.id, content })
  }

  let requested = []
  if (isComment) {
    const tokens = parseCommand(context.payload.comment.body)
    if (tokens === null) return core.info('Comment is not an /ax-eval command')
    requested = tokens
  }

  let role = 'none'
  try {
    const { data: permission } = await github.rest.repos.getCollaboratorPermissionLevel({ ...repo, username: actor })
    role = permission.role_name
  } catch (error) {
    // 404 for users who are not collaborators at all.
    core.info(`Could not read @${actor}'s permission: ${error.message}`)
  }
  if (!WRITE_ROLES.has(role)) {
    await react('-1')
    return core.notice(`@${actor} has '${role}' access; AX evals need write access`)
  }

  const { data: pr } = await github.rest.pulls.get({ ...repo, pull_number: prNumber })
  if (pr.state !== 'open') return core.notice(`PR #${prNumber} is ${pr.state}`)
  if (!pr.head.repo || pr.head.repo.full_name !== `${repo.owner}/${repo.repo}`) {
    await react('confused')
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
      await react('confused')
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
    await react('eyes')
    await upsertComment(github, repo, prNumber, context.runId, [
      '### AX evals (2027.dev)',
      '',
      `This PR doesn't change any SDK or CLI sources (commit \`${sha.slice(0, 7)}\`), so nothing was evaluated.`,
      'Comment `/ax-eval all` or `/ax-eval <python|typescript|go|ruby|java|cli>` to run evals anyway.',
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
  await react('rocket')
  await upsertComment(github, repo, prNumber, context.runId, [
    '### AX evals (2027.dev)',
    '',
    `⏳ Running ${matrix.map((m) => m.title).join(', ')} on commit \`${sha.slice(0, 7)}\`. Results usually take 7–10 minutes, longer when the 2027 queue is busy ([workflow run](${runUrl})).`,
  ])
}

module.exports = run
module.exports.parseCommand = parseCommand
module.exports.selectByPaths = selectByPaths
module.exports.MARKER = MARKER
